"""Parse một tệp nguồn cho S6 (spec §7.6; preflight F09, F10; QĐ-34).

Chọn cách parse theo đuôi khóa lưu trữ:

- `.pdf` (bản mở, tệp tải lên): như PDF của bài ở S1 — GROBID, `pypdfium2` dự phòng — nhưng
  giới hạn `max_source_pages` thay cho `max_pages`, kiểm tra trước khi gọi GROBID.
- `.abstract.json` (S5): `{"title", "abstract"}` → một đoạn, mục `"Abstract"`.
- `.txt` (nguồn của bộ đánh giá): văn bản UTF-8, đoạn tách theo dòng trống.

Tệp không đọc được (`InputError`: PDF mã hóa, hỏng, toàn trang quét, quá dài; `ParseError`),
đuôi không hỗ trợ, hoặc không có chữ nào → None: S6 không tạo chunk và S8 ra
`SOURCE_PARSE_FAILED`; lần chạy không lỗi vì một nguồn hỏng. GROBID không truy cập được vẫn
ném `RetryableError` (job runner thử lại) trừ khi `allow_fallback`. Không log nội dung nguồn.
"""

import asyncio
import json
import logging
from typing import Literal

from citeproof.core.errors import InputError, ParseError
from citeproof.db.models import SourceFile
from citeproof.parsing.model import ParagraphDraft, ParsedDocument
from citeproof.parsing.pdf import parse_pdf_document, pdf_page_count
from citeproof.parsing.tei import ABSTRACT_SECTION
from citeproof.parsing.text import parse_plain_text
from citeproof.pipeline.deps import PipelineDeps

logger = logging.getLogger(__name__)

PARSED_SUFFIX = ".parsed.json"
ABSTRACT_SUFFIX = ".abstract.json"

SourceFormat = Literal["pdf", "abstract", "text"]


def parsed_source_key(storage_key: str) -> str:
    """Khóa JSON đã parse (`ParsedDocument`) của một tệp nguồn, cạnh tệp gốc."""
    return storage_key + PARSED_SUFFIX


def source_format(storage_key: str) -> SourceFormat | None:
    key = storage_key.lower()
    if key.endswith(ABSTRACT_SUFFIX):
        return "abstract"
    if key.endswith(".pdf"):
        return "pdf"
    if key.endswith(".txt"):
        return "text"
    return None


def parse_abstract(data: bytes) -> ParsedDocument:
    """JSON abstract của S5 → một đoạn thuộc mục `"Abstract"`."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError("Không đọc được abstract đã lưu.", code="ABSTRACT_INVALID") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("abstract"), str):
        raise ParseError("Không đọc được abstract đã lưu.", code="ABSTRACT_INVALID")
    draft = ParagraphDraft(text=payload["abstract"], section=ABSTRACT_SECTION, page=None)
    return ParsedDocument.build(source="text", drafts=[draft], page_count=None)


async def _parse_pdf(deps: PipelineDeps, data: bytes, *, allow_fallback: bool) -> ParsedDocument:
    pages = await asyncio.to_thread(pdf_page_count, data)  # PDF mã hóa/hỏng: InputError
    limit = deps.settings.max_source_pages
    if pages > limit:
        raise InputError(
            f"Nguồn có {pages} trang, vượt giới hạn {limit} trang.", code="PAGE_LIMIT_EXCEEDED"
        )
    doc, _warnings = await parse_pdf_document(
        data, grobid=deps.grobid, allow_fallback=allow_fallback
    )
    return doc


async def parse_source(
    deps: PipelineDeps, source: SourceFile, *, allow_fallback: bool
) -> ParsedDocument | None:
    """`ParsedDocument` của tệp nguồn, hoặc None khi tệp không cho ra chữ (xem docstring module)."""
    kind = source_format(source.storage_key)
    if kind is None:
        logger.warning("source_format_unsupported", extra={"error_type": "UnsupportedFormat"})
        return None
    data = await deps.storage.get(source.storage_key)
    try:
        if kind == "pdf":
            doc = await _parse_pdf(deps, data, allow_fallback=allow_fallback)
        elif kind == "abstract":
            doc = await asyncio.to_thread(parse_abstract, data)
        else:
            doc = await asyncio.to_thread(parse_plain_text, data)
    except (InputError, ParseError) as exc:
        logger.warning(
            "source_parse_failed code=%s", exc.code, extra={"error_type": type(exc).__name__}
        )
        return None
    return doc if doc.paragraphs else None
