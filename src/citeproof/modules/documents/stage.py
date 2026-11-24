"""S1 — nhận và parse tài liệu (spec §7.1; T-201, T-203, T-204, T-205, T-206).

Đọc tệp gốc từ storage, parse (văn bản JSON, DOCX, hoặc PDF qua GROBID với `pypdfium2`
dự phòng), ghi `documents/<uid>/<docid>/parsed.json` rồi cập nhật `documents.parsed_key`,
`language`, `page_count` và nối cảnh báo vào `parse_warnings` trong một giao dịch có rào
lease. Gọi GROBID và parse đều xong trước giao dịch đó. Kết quả cấp tài liệu tạo một lần
(QĐ-13): đã có `parsed_key` thì bỏ qua. Không gọi LLM; không log nội dung tài liệu.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update

from citeproof.core.enums import DocumentKind, StageName
from citeproof.core.errors import InputError, NotFoundError, ParseError
from citeproof.db.models import Document, VerificationRun
from citeproof.parsing.docx import parse_docx
from citeproof.parsing.encoding import detect_legacy_encoding
from citeproof.parsing.model import ParsedDocument
from citeproof.parsing.pdf import parse_pdf_document, pdf_page_count
from citeproof.parsing.text import parse_text_payload
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.storage import document_prefix

# Trùng `services.documents.MIME` (module không phụ thuộc tầng services).
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
LEGACY_ENCODING = "LEGACY_ENCODING"

InputSource = Literal["text", "docx", "pdf"]


def parsed_key(user_id: UUID, document_id: UUID) -> str:
    return f"{document_prefix(user_id, document_id)}/parsed.json"


def merge_parse_warnings(existing: Sequence[Any], new: Sequence[Mapping[str, Any]]) -> list[Any]:
    """Nối cảnh báo mới vào `parse_warnings`, bỏ mã đã có (giữ `SAMPLE_DOCUMENT`).

    Mục đã có giữ nguyên vị trí và nội dung; trả danh sách mới, không sửa đầu vào.
    """
    merged = list(existing)
    codes = {item.get("code") for item in merged if isinstance(item, Mapping)}
    for warning in new:
        code = warning.get("code")
        if code not in codes:
            merged.append(dict(warning))
            codes.add(code)
    return merged


def legacy_encoding_warnings(doc: ParsedDocument) -> list[dict[str, Any]]:
    """`{"code": "LEGACY_ENCODING", "encoding": ...}` khi thân bài hoặc danh mục dùng bảng mã cũ."""
    text = "\n".join([doc.text(), *(ref.raw for ref in doc.references_raw or [])])
    encoding = detect_legacy_encoding(text)
    return [] if encoding is None else [{"code": LEGACY_ENCODING, "encoding": encoding}]


def _parse_input(
    source: Literal["text", "docx"], data: bytes
) -> tuple[ParsedDocument, list[dict[str, Any]]]:
    doc = parse_text_payload(data) if source == "text" else parse_docx(data)
    return doc, legacy_encoding_warnings(doc)


@dataclass(frozen=True)
class _DocumentRow:
    id: UUID
    user_id: UUID
    kind: str
    mime_type: str
    storage_key: str
    parsed_key: str | None


def _input_source(document: _DocumentRow) -> InputSource:
    if document.kind == DocumentKind.TEXT.value:
        return "text"
    if document.mime_type == DOCX_MIME:
        return "docx"
    if document.mime_type == PDF_MIME:
        return "pdf"
    raise ParseError("Định dạng tệp chưa được hỗ trợ.", code="UNSUPPORTED_FILE_TYPE")


class ParseStage:
    name: StageName = StageName.S1

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def _parse_pdf(
        self, data: bytes, ctx: StageContext
    ) -> tuple[ParsedDocument, list[dict[str, Any]]]:
        """Giới hạn trang, rồi GROBID; chỉ dùng `pypdfium2` khi GROBID lỗi xử lý tài liệu,
        hoặc không truy cập được ở lần thử cuối (QĐ-22), hoặc không được cấu hình.
        """
        pages = await asyncio.to_thread(pdf_page_count, data)  # PDF mã hóa: PDF_ENCRYPTED
        max_pages = self._deps.settings.max_pages
        if pages > max_pages:
            raise InputError(
                f"Tệp có {pages} trang, vượt giới hạn {max_pages} trang.",
                code="PAGE_LIMIT_EXCEEDED",
            )
        grobid = self._deps.grobid
        parsed, warnings = await parse_pdf_document(
            data, grobid=grobid, allow_fallback=ctx.is_last_attempt or grobid is None
        )
        legacy = await asyncio.to_thread(legacy_encoding_warnings, parsed)
        return parsed, [*warnings, *legacy]

    async def _load(self, run_id: UUID) -> _DocumentRow:
        async with self._deps.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        Document.id,
                        Document.user_id,
                        Document.kind,
                        Document.mime_type,
                        Document.storage_key,
                        Document.parsed_key,
                    )
                    .join(VerificationRun, VerificationRun.document_id == Document.id)
                    .where(VerificationRun.id == run_id)
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("Không tìm thấy lần chạy")
        return _DocumentRow(
            id=row.id,
            user_id=row.user_id,
            kind=row.kind,
            mime_type=row.mime_type,
            storage_key=row.storage_key,
            parsed_key=row.parsed_key,
        )

    async def run(self, ctx: StageContext) -> StageResult:
        document = await self._load(ctx.run_id)
        if document.parsed_key is not None:
            return StageResult()
        source = _input_source(document)
        data = await self._deps.storage.get(document.storage_key)
        if source == "pdf":
            parsed, warnings = await self._parse_pdf(data, ctx)
        else:
            # Parse DOCX và dò bảng mã trên văn bản dài tốn CPU: chạy ngoài event loop để
            # vòng heartbeat vẫn gia hạn được lease.
            parsed, warnings = await asyncio.to_thread(_parse_input, source, data)
        key = parsed_key(document.user_id, document.id)
        # Ghi storage trước giao dịch rào (giữ giao dịch ngắn); thử lại thì ghi đè.
        await self._deps.storage.put(key, parsed.model_dump_json().encode())
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            current = (
                await session.execute(
                    select(Document.parsed_key, Document.parse_warnings)
                    .where(Document.id == document.id)
                    .with_for_update()
                )
            ).one()
            if current.parsed_key is not None:
                return StageResult()
            await session.execute(
                update(Document)
                .where(Document.id == document.id)
                .values(
                    parsed_key=key,
                    language=parsed.language.value,
                    page_count=parsed.page_count,
                    parse_warnings=merge_parse_warnings(current.parse_warnings or [], warnings),
                )
            )
        return StageResult()
