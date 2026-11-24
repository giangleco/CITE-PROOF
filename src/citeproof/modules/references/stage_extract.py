"""S2 — tách và parse danh mục tài liệu tham khảo (spec §7.2; T-207, QĐ-04, QĐ-13).

Đơn vị là tài liệu: `reference_entries` tạo một lần, lần chạy sau thấy đã có thì bỏ qua.
Mọi việc chậm (đọc storage, GROBID, W1) xong trước giao dịch rào lease; parse JSON, rule và
TEI chạy trong `asyncio.to_thread`. Ghi mọi mục trong một giao dịch, khóa dòng tài liệu để
hai lần chạy song song không ghi trùng.

Trường mỗi mục:
1. Trường GROBID: đã có từ S1 (`RawReference.parsed`), hoặc `processCitationList` cho chuỗi
   chưa có cấu trúc (nhãn `[n]` bị bỏ trước khi gửi, vì GROBID làm hỏng chuỗi mở đầu bằng
   `[`). GROBID tạm thời không truy cập được → job thử lại; ở lần thử cuối, hoặc khi GROBID
   lỗi xử lý/TEI không khớp số chuỗi, dùng rule.
2. Rule lấp trường GROBID thiếu; tác giả ưu tiên khối tác giả của rule (giữ cách viết trong
   bài, GROBID hay tách sai tên người Việt). Tiêu đề bỏ phần xuất bản dính sau dấu phẩy.
3. W1 cho mục vẫn chưa có tiêu đề lẫn DOI, khi có LLM: trường đã kiểm lấp chỗ trống, loại
   tài liệu của W1 ghi đè rule. Không có LLM → cảnh báo `LLM_NOT_CONFIGURED` (chỉ khi có mục
   như vậy). LLM tạm thời lỗi ở lần thử cuối → giữ kết quả rule.

`parse_method` là nguồn của tiêu đề (`grobid` | `rule` | `llm`; không có tiêu đề → `rule`).
Không có danh mục → `{"code": "NO_REFERENCE_SECTION"}` vào `documents.parse_warnings`.
Không log chuỗi tài liệu tham khảo, chỉ số lượng và loại lỗi.
"""

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import LLMTask, ParseMethod, RefType, StageName
from citeproof.core.errors import InternalError, NotFoundError, ParseError, RetryableError
from citeproof.db.models import Document, ReferenceEntry, VerificationRun
from citeproof.llm.client import LLMClient
from citeproof.modules.references.classify import classify_ref_type
from citeproof.modules.references.llm_parse import parse_with_llm
from citeproof.modules.references.normalize import nfc_reference, normalize_doi
from citeproof.modules.references.rules import clean_title, parse_reference_rules
from citeproof.modules.references.sections import document_references
from citeproof.parsing.model import ParsedDocument, RawReference
from citeproof.parsing.tei import parse_citation_list_tei
from citeproof.pipeline.config import load_run_info
from citeproof.pipeline.deps import PipelineDeps, call_context
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult

logger = logging.getLogger(__name__)

NO_REFERENCE_SECTION = "NO_REFERENCE_SECTION"
LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
_LEADING_LABEL = re.compile(r"^\s*\[\d{1,4}\]\s*")


@dataclass(frozen=True)
class _DocumentRow:
    id: UUID
    parsed_key: str | None
    has_entries: bool


@dataclass(frozen=True)
class EntryDraft:
    raw: str
    parsed: ParsedReference
    ref_type: RefType
    method: ParseMethod


def merge_fields(
    structured: ParsedReference | None, rule: ParsedReference, *, label: str | None
) -> tuple[ParsedReference, ParseMethod]:
    """Gộp trường GROBID (nếu có) với trường rule; trả kèm nguồn của tiêu đề."""
    base = structured or ParsedReference()
    grobid_title = clean_title(base.title)
    title = grobid_title or rule.title
    method = ParseMethod.GROBID if grobid_title else ParseMethod.RULE
    doi = base.doi if normalize_doi(base.doi) is not None else rule.doi
    merged = ParsedReference(
        title=title,
        authors=rule.authors or base.authors,
        year=base.year if base.year is not None else rule.year,
        venue=base.venue,
        doi=doi,
        isbn=base.isbn or rule.isbn,
        url=base.url or rule.url,
        label=label,
    )
    return nfc_reference(merged), method


def draft_entries(
    references: Sequence[RawReference], grobid: Mapping[int, ParsedReference]
) -> list[EntryDraft]:
    """Bản nháp mục từ trường GROBID và rule (chạy trong thread: thuần CPU)."""
    drafts: list[EntryDraft] = []
    for index, ref in enumerate(references):
        structured = ref.parsed or grobid.get(index)
        parsed, method = merge_fields(structured, parse_reference_rules(ref.raw), label=ref.label)
        drafts.append(EntryDraft(ref.raw, parsed, classify_ref_type(ref.raw, parsed), method))
    return drafts


def apply_w1(draft: EntryDraft, w1: ParsedReference, ref_type: RefType) -> EntryDraft:
    """Kết quả W1 đã kiểm lấp trường còn trống; loại tài liệu của W1 ghi đè rule."""
    parsed = draft.parsed
    title = parsed.title or clean_title(w1.title)
    merged = parsed.model_copy(
        update={
            "title": title,
            "authors": parsed.authors or w1.authors,
            "year": parsed.year if parsed.year is not None else w1.year,
            "venue": parsed.venue or w1.venue,
            "doi": parsed.doi or w1.doi,
            "isbn": parsed.isbn or w1.isbn,
            "url": parsed.url or w1.url,
        }
    )
    method = ParseMethod.LLM if parsed.title is None and title is not None else draft.method
    return EntryDraft(draft.raw, nfc_reference(merged), ref_type, method)


def _needs_llm(draft: EntryDraft) -> bool:
    return draft.parsed.title is None and normalize_doi(draft.parsed.doi) is None


def merge_parse_warning(existing: Sequence[Any], code: str) -> list[Any]:
    """Thêm `{"code": code}` vào `parse_warnings` nếu chưa có mã đó."""
    merged = list(existing)
    if not any(isinstance(item, Mapping) and item.get("code") == code for item in merged):
        merged.append({"code": code})
    return merged


class ExtractReferencesStage:
    name: StageName = StageName.S2

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def _load(self, run_id: UUID) -> _DocumentRow:
        entry_exists = (
            select(ReferenceEntry.id).where(ReferenceEntry.document_id == Document.id).exists()
        )
        async with self._deps.session_factory() as session:
            row = (
                await session.execute(
                    select(Document.id, Document.parsed_key, entry_exists.label("has_entries"))
                    .join(VerificationRun, VerificationRun.document_id == Document.id)
                    .where(VerificationRun.id == run_id)
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("Không tìm thấy lần chạy")
        return _DocumentRow(id=row.id, parsed_key=row.parsed_key, has_entries=row.has_entries)

    async def _grobid_fields(
        self, references: Sequence[RawReference], ctx: StageContext
    ) -> dict[int, ParsedReference]:
        """Trường `processCitationList` cho chuỗi chưa có cấu trúc, theo chỉ số mục."""
        grobid = self._deps.grobid
        pending = [index for index, ref in enumerate(references) if ref.parsed is None]
        if grobid is None or not pending:
            return {}
        strings = [_LEADING_LABEL.sub("", references[index].raw) for index in pending]
        try:
            tei = await grobid.process_citations(strings)
            fields = await asyncio.to_thread(parse_citation_list_tei, tei)
        except RetryableError as exc:
            if not ctx.is_last_attempt:
                raise
            logger.warning("grobid_citations_skipped", extra={"error_type": type(exc).__name__})
            return {}
        except ParseError as exc:
            logger.warning("grobid_citations_skipped", extra={"error_type": type(exc).__name__})
            return {}
        if len(fields) != len(strings):
            logger.warning("grobid_citations_misaligned")
            return {}
        return dict(zip(pending, fields, strict=True))

    async def _apply_llm(self, llm: LLMClient, drafts: list[EntryDraft], ctx: StageContext) -> None:
        pending = [index for index, draft in enumerate(drafts) if _needs_llm(draft)]
        async with self._deps.session_factory() as session:
            info = await load_run_info(session, ctx.run_id)
        try:
            results = await parse_with_llm(
                llm,
                self._deps.prompts,
                info.config.task(LLMTask.REFERENCE_PARSE),
                call_context(self._deps, info, StageName.S2),
                [drafts[index].raw for index in pending],
            )
        except RetryableError as exc:
            if not ctx.is_last_attempt:
                raise
            logger.warning("w1_skipped", extra={"error_type": type(exc).__name__})
            return
        for index, result in zip(pending, results, strict=True):
            if result is not None:
                drafts[index] = apply_w1(drafts[index], *result)

    async def _write_entries(
        self, ctx: StageContext, document_id: UUID, drafts: Sequence[EntryDraft]
    ) -> None:
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            await session.execute(
                select(Document.id).where(Document.id == document_id).with_for_update()
            )
            existing = await session.scalar(
                select(ReferenceEntry.id).where(ReferenceEntry.document_id == document_id).limit(1)
            )
            if existing is not None:
                return  # lần chạy khác của cùng tài liệu đã ghi trước (QĐ-13)
            session.add_all(
                ReferenceEntry(
                    document_id=document_id,
                    order_index=order,
                    raw_text=draft.raw,
                    parsed=draft.parsed.model_dump(mode="json"),
                    ref_type=draft.ref_type.value,
                    parse_method=draft.method.value,
                    doi_normalized=normalize_doi(draft.parsed.doi),
                )
                for order, draft in enumerate(drafts)
            )

    async def _write_no_section(self, ctx: StageContext, document_id: UUID) -> None:
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            warnings = await session.scalar(
                select(Document.parse_warnings).where(Document.id == document_id).with_for_update()
            )
            merged = merge_parse_warning(warnings or [], NO_REFERENCE_SECTION)
            if merged != warnings:
                await session.execute(
                    update(Document).where(Document.id == document_id).values(parse_warnings=merged)
                )

    async def run(self, ctx: StageContext) -> StageResult:
        document = await self._load(ctx.run_id)
        if document.has_entries:
            return StageResult()
        if document.parsed_key is None:
            raise InternalError("S2 chạy khi tài liệu chưa được parse.")
        data = await self._deps.storage.get(document.parsed_key)
        parsed = await asyncio.to_thread(ParsedDocument.model_validate_json, data)
        references = await asyncio.to_thread(document_references, parsed)
        if not references:
            await self._write_no_section(ctx, document.id)
            return StageResult()
        grobid = await self._grobid_fields(references, ctx)
        drafts = await asyncio.to_thread(draft_entries, references, grobid)
        warnings: tuple[str, ...] = ()
        if any(_needs_llm(draft) for draft in drafts):
            if self._deps.llm is None:
                warnings = (LLM_NOT_CONFIGURED,)
            else:
                await self._apply_llm(self._deps.llm, drafts, ctx)
        await self._write_entries(ctx, document.id, drafts)
        return StageResult(warnings=warnings)
