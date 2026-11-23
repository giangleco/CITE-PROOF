"""Nhận tài liệu (file hoặc văn bản), tạo lần chạy, liệt kê tài liệu (spec web UI §7.3).

File được nhận diện bằng chữ ký, không tin phần mở rộng hay MIME trình duyệt gửi.
Khóa lưu trữ tạo từ UUID; tên file gốc chỉ để hiển thị.
"""

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import (
    DocumentKind,
    JobStatus,
    RunStatus,
    StageName,
    UserRole,
    Verifier,
)
from citeproof.core.errors import ConflictError, InputError, NotFoundError, RateLimitedError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, Job, User, VerificationRun
from citeproof.pipeline.runs import create_run
from citeproof.storage import Storage, document_prefix

FileKind = Literal["pdf", "docx"]
MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SAMPLE_CODE = "SAMPLE_DOCUMENT"
ADMIN_VARIANTS = (Verifier.V0, Verifier.V1, Verifier.V2)
DEFAULT_VARIANT = Verifier.V2
_ACTIVE = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
_CHUNK = 65_536
_MAX_NAME = 200
_VN_TZ = timezone(timedelta(hours=7))  # Việt Nam không có giờ mùa hè.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class DemoConfig:
    step_delay_s: float
    fail_at: StageName | None

    def to_json(self) -> dict[str, Any]:
        return {
            "step_delay_s": self.step_delay_s,
            "fail_at": self.fail_at.value if self.fail_at else None,
        }


@dataclass(frozen=True)
class DocumentRow:
    id: UUID
    name: str
    kind: DocumentKind
    is_sample: bool
    created_at: datetime
    latest_run_id: UUID | None
    latest_run_status: RunStatus | None
    done_stages: int


def sniff_kind(data: bytes) -> FileKind | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, ValueError):
            return None
        if {"[Content_Types].xml", "word/document.xml"} <= names:
            return "docx"
    return None


def clean_filename(name: str | None) -> str:
    text = unicodedata.normalize("NFC", name or "")
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = _CONTROL.sub("", text).strip()
    return text[:_MAX_NAME] or "tai-lieu"


async def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise InputError(
                f"Tệp vượt quá giới hạn {max_bytes // 1_048_576} MB.", code="FILE_TOO_LARGE"
            )
        chunks.append(chunk)
    if total == 0:
        raise InputError("Tệp rỗng. Hãy chọn một tệp PDF hoặc DOCX.", code="EMPTY_FILE")
    return b"".join(chunks)


def _vn_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def is_sample(document: Document) -> bool:
    return any(
        isinstance(item, dict) and item.get("code") == SAMPLE_CODE
        for item in document.parse_warnings or []
    )


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


async def start_run(
    session: AsyncSession,
    settings: Settings,
    *,
    user: User,
    document: Document,
    variant: Verifier = DEFAULT_VARIANT,
    demo: DemoConfig | None = None,
) -> UUID:
    """Tạo lần chạy mới; khóa dòng tài liệu để hai yêu cầu đồng thời chỉ tạo một lần chạy."""
    locked = (
        await session.execute(
            select(Document)
            .where(Document.id == document.id, Document.user_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise NotFoundError()
    if is_sample(locked):
        raise InputError("Bài mẫu không kiểm tra lại được.", code="SAMPLE_NOT_RERUNNABLE")
    active = await session.scalar(
        select(VerificationRun.id)
        .where(VerificationRun.document_id == locked.id, VerificationRun.status.in_(_ACTIVE))
        .limit(1)
    )
    if active is not None:
        raise ConflictError(
            "Bài này đang được kiểm tra. Chờ lần kiểm tra xong rồi thử lại.",
            code="RUN_IN_PROGRESS",
        )
    if not _is_admin(user):
        since = datetime.now(UTC) - timedelta(days=1)
        used = await session.scalar(
            select(func.count())
            .select_from(VerificationRun)
            .where(VerificationRun.user_id == user.id, VerificationRun.created_at > since)
        )
        if int(used or 0) >= settings.runs_per_day:
            raise RateLimitedError(
                f"Bạn đã dùng hết {settings.runs_per_day} lần kiểm tra trong 24 giờ qua. "
                "Thử lại sau.",
                code="QUOTA_EXCEEDED",
            )
    chosen = variant if _is_admin(user) and variant in ADMIN_VARIANTS else DEFAULT_VARIANT
    config: dict[str, Any] = {"variant": chosen.value}
    if demo is not None and settings.env == "local":
        config["demo"] = demo.to_json()
    return await create_run(session, document_id=locked.id, user_id=user.id, config=config)


async def _store_and_start(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    user: User,
    document: Document,
    data: bytes,
    variant: Verifier,
    demo: DemoConfig | None,
) -> tuple[Document, UUID]:
    session.add(document)
    await session.flush()
    run_id = await start_run(
        session, settings, user=user, document=document, variant=variant, demo=demo
    )
    await storage.put(document.storage_key, data)
    return document, run_id


async def create_file_document(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    user: User,
    filename: str | None,
    data: bytes,
    variant: Verifier = DEFAULT_VARIANT,
    demo: DemoConfig | None = None,
) -> tuple[Document, UUID]:
    kind = sniff_kind(data)
    if kind is None:
        raise InputError(
            "Chỉ nhận tệp PDF hoặc DOCX. Tệp này không phải PDF hay DOCX hợp lệ.",
            code="UNSUPPORTED_FILE_TYPE",
        )
    document_id = uuid4()
    document = Document(
        id=document_id,
        user_id=user.id,
        kind=DocumentKind.FILE.value,
        filename=clean_filename(filename),
        mime_type=MIME[kind],
        storage_key=f"{document_prefix(user.id, document_id)}/original.{kind}",
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return await _store_and_start(
        session,
        storage,
        settings,
        user=user,
        document=document,
        data=data,
        variant=variant,
        demo=demo,
    )


async def create_text_document(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    user: User,
    title: str | None,
    body: str,
    references: str,
    variant: Verifier = DEFAULT_VARIANT,
    demo: DemoConfig | None = None,
) -> tuple[Document, UUID]:
    body = unicodedata.normalize("NFC", body).strip()
    references = unicodedata.normalize("NFC", references).strip()
    if not body or not references:
        raise InputError(
            "Cần cả đoạn văn có trích dẫn và danh mục tài liệu tham khảo.", code="EMPTY_TEXT"
        )
    if len(body) > settings.max_text_chars or len(references) > settings.max_reference_chars:
        raise InputError(
            f"Đoạn văn tối đa {_vn_number(settings.max_text_chars)} ký tự, danh mục tối đa "
            f"{_vn_number(settings.max_reference_chars)} ký tự.",
            code="TEXT_TOO_LONG",
        )
    name = clean_filename(title) if title and title.strip() else None
    if name is None:
        name = "Đoạn văn " + datetime.now(_VN_TZ).strftime("%d/%m/%Y %H:%M")
    data = json.dumps({"body": body, "references": references}, ensure_ascii=False).encode()
    document_id = uuid4()
    document = Document(
        id=document_id,
        user_id=user.id,
        kind=DocumentKind.TEXT.value,
        filename=name,
        mime_type="application/json",
        storage_key=f"{document_prefix(user.id, document_id)}/original.json",
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return await _store_and_start(
        session,
        storage,
        settings,
        user=user,
        document=document,
        data=data,
        variant=variant,
        demo=demo,
    )


async def get_own_document(session: AsyncSession, *, user: User, document_id: UUID) -> Document:
    document = (
        await session.execute(
            select(Document).where(Document.id == document_id, Document.user_id == user.id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError()
    return document


async def list_documents(session: AsyncSession, *, user: User) -> list[DocumentRow]:
    docs = (
        (
            await session.execute(
                select(Document)
                .where(Document.user_id == user.id)
                .order_by(Document.created_at.desc(), Document.id)
            )
        )
        .scalars()
        .all()
    )
    latest = (
        (
            await session.execute(
                select(VerificationRun)
                .where(VerificationRun.user_id == user.id)
                .ext(distinct_on(VerificationRun.document_id))
                .order_by(VerificationRun.document_id, VerificationRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    run_by_doc = {run.document_id: run for run in latest}
    done: dict[UUID, int] = {}
    if latest:
        rows = await session.execute(
            select(Job.run_id, func.count())
            .where(Job.run_id.in_([run.id for run in latest]), Job.status == JobStatus.DONE.value)
            .group_by(Job.run_id)
        )
        done = {run_id: int(count) for run_id, count in rows}
    result: list[DocumentRow] = []
    for doc in docs:
        run = run_by_doc.get(doc.id)
        result.append(
            DocumentRow(
                id=doc.id,
                name=doc.filename,
                kind=DocumentKind(doc.kind),
                is_sample=is_sample(doc),
                created_at=doc.created_at,
                latest_run_id=run.id if run else None,
                latest_run_status=RunStatus(run.status) if run else None,
                done_stages=done.get(run.id, 0) if run else 0,
            )
        )
    return result
