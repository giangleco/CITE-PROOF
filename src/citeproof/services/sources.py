"""Tải lên toàn văn cho tài liệu tham khảo còn thiếu (FR-32, FR-33, T-404).

Bản tải lên chỉ thuộc về người tải: `origin = USER_UPLOAD`, `owner_user_id`, gắn với một
`reference_entry`. File lưu dưới `documents/<uid>/<docid>/sources/`, nên xóa tài liệu
sẽ xóa cả bản tải lên. Không log tiêu đề hay nội dung file.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import AccessStatus, ContentLevel, SourceOrigin, TitleMatch
from citeproof.core.errors import InputError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    ReferenceCheck,
    ReferenceEntry,
    SourceFile,
    User,
    VerificationRun,
    Work,
)
from citeproof.modules.sources.title_match import title_match
from citeproof.parsing.pdf import pdf_page_count
from citeproof.services.documents import sniff_kind
from citeproof.storage import Storage, document_prefix

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadedSource:
    source_file_id: UUID
    reference_entry_id: UUID
    title_match: TitleMatch
    created_at: datetime


def _view(row: SourceFile) -> UploadedSource:
    assert row.reference_entry_id is not None  # ràng buộc `user_upload_needs_owner_and_reference`
    return UploadedSource(
        source_file_id=row.id,
        reference_entry_id=row.reference_entry_id,
        title_match=TitleMatch(row.title_match),
        created_at=row.created_at,
    )


async def _own_run_document(session: AsyncSession, *, user: User, run_id: UUID) -> UUID:
    document_id = await session.scalar(
        select(VerificationRun.document_id).where(
            VerificationRun.id == run_id, VerificationRun.user_id == user.id
        )
    )
    if document_id is None:
        raise NotFoundError()
    return document_id


async def _reference_title(
    session: AsyncSession, entry: ReferenceEntry, run_id: UUID
) -> str | None:
    parsed = entry.parsed.get("title") if isinstance(entry.parsed, dict) else None
    if isinstance(parsed, str) and parsed.strip():
        return parsed
    return await session.scalar(
        select(Work.title)
        .join(ReferenceCheck, ReferenceCheck.work_id == Work.id)
        .where(ReferenceCheck.run_id == run_id, ReferenceCheck.reference_entry_id == entry.id)
    )


async def upload_source(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    *,
    user: User,
    run_id: UUID,
    reference_entry_id: UUID,
    data: bytes,
) -> UploadedSource:
    document_id = await _own_run_document(session, user=user, run_id=run_id)
    entry = (
        await session.execute(
            select(ReferenceEntry).where(
                ReferenceEntry.id == reference_entry_id, ReferenceEntry.document_id == document_id
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError()
    if len(data) > settings.max_upload_bytes:
        raise InputError(
            f"Tệp vượt quá giới hạn {settings.max_upload_bytes // 1_048_576} MB.",
            code="FILE_TOO_LARGE",
        )
    if sniff_kind(data) != "pdf":
        raise InputError(
            "Chỉ nhận tệp PDF cho nguồn tải lên. Tệp này không phải PDF hợp lệ.",
            code="UNSUPPORTED_FILE_TYPE",
        )
    pages = await asyncio.to_thread(pdf_page_count, data)
    if pages > settings.max_pages:
        raise InputError(
            f"Tệp PDF có {pages} trang, vượt giới hạn {settings.max_pages} trang.",
            code="PAGE_LIMIT_EXCEEDED",
        )
    match = await asyncio.to_thread(
        title_match, data, await _reference_title(session, entry, run_id)
    )
    source_file_id = uuid4()
    key = f"{document_prefix(user.id, document_id)}/sources/{source_file_id}.pdf"
    row = SourceFile(
        id=source_file_id,
        reference_entry_id=entry.id,
        owner_user_id=user.id,
        origin=SourceOrigin.USER_UPLOAD.value,
        access_status=AccessStatus.USER_PROVIDED.value,
        content_level=ContentLevel.FULL_TEXT.value,
        storage_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        title_match=match.value,
    )
    session.add(row)
    await session.flush()
    await storage.put(key, data)
    return _view(row)


async def discard_source(
    session: AsyncSession,
    storage: Storage,
    *,
    user: User,
    run_id: UUID,
    source_file_id: UUID,
) -> None:
    document_id = await _own_run_document(session, user=user, run_id=run_id)
    row = (
        await session.execute(
            select(SourceFile)
            .join(ReferenceEntry, ReferenceEntry.id == SourceFile.reference_entry_id)
            .where(
                SourceFile.id == source_file_id,
                SourceFile.origin == SourceOrigin.USER_UPLOAD.value,
                SourceFile.owner_user_id == user.id,
                ReferenceEntry.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError()
    key = row.storage_key
    await session.execute(delete(SourceFile).where(SourceFile.id == row.id))
    await session.commit()
    try:
        await storage.delete(key)
    except (OSError, ValueError) as exc:
        logger.warning("source_delete_failed", extra={"error_type": type(exc).__name__})


async def uploads_for_document(
    session: AsyncSession, *, user_id: UUID, document_id: UUID
) -> dict[UUID, UploadedSource]:
    """Bản tải lên mới nhất của người dùng cho từng tài liệu tham khảo (một truy vấn)."""
    rows = (
        await session.execute(
            select(SourceFile)
            .join(ReferenceEntry, ReferenceEntry.id == SourceFile.reference_entry_id)
            .where(
                SourceFile.origin == SourceOrigin.USER_UPLOAD.value,
                SourceFile.owner_user_id == user_id,
                ReferenceEntry.document_id == document_id,
            )
            .order_by(SourceFile.created_at, SourceFile.id)
        )
    ).scalars()
    return {view.reference_entry_id: view for view in map(_view, rows)}  # bản sau ghi đè


async def uploads_for_run(
    session: AsyncSession, *, user: User, run_id: UUID
) -> dict[UUID, UploadedSource]:
    document_id = await _own_run_document(session, user=user, run_id=run_id)
    return await uploads_for_document(session, user_id=user.id, document_id=document_id)
