"""S4 — trích dẫn, liên kết, claim (spec §7.4, §6.4; T-401, T-402, QĐ-13, QĐ-16, QĐ-23).

Đơn vị là tài liệu: `citations`, `citation_links`, `claims` tạo một lần cho tài liệu; lần chạy
sau thấy đã có `citations` thì bỏ qua (QĐ-13). Không có `reference_entries` (bài không có
danh mục) thì S4 không có việc (spec §7.2).

Đọc `ParsedDocument` từ storage và các mục tài liệu tham khảo trước; tách câu, tìm marker,
liên kết và tính span (`build_drafts`) chạy trong `asyncio.to_thread`. Ghi mọi dòng trong
một giao dịch rào lease, khóa dòng tài liệu để hai lần chạy song song của cùng tài liệu không
ghi trùng. Mỗi marker một `citations` (vị trí marker trong `ParsedDocument.text()`, trang,
mục, câu), mỗi mục được trích một `citation_links` (`reference_entry_id` NULL khi không liên
kết được; `link_method` theo loại marker), một `claims` (`extraction_method = "rule"`).

Bước rule không gọi mạng hay LLM nên không có lỗi theo mục để cô lập (preflight F13 áp cho
W2 ở Task 11): lỗi nào cũng làm stage lỗi. Không log nội dung bài.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from citeproof.core.enums import StageName
from citeproof.core.errors import InternalError, NotFoundError
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    Document,
    ReferenceEntry,
    VerificationRun,
)
from citeproof.modules.citations.claims import ClaimDraft, build_drafts
from citeproof.modules.citations.linking import LinkTarget, ReferenceIndex, make_target
from citeproof.parsing.model import ParsedDocument
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult

Drafts = Sequence[tuple[ClaimDraft, list[UUID | None]]]


@dataclass(frozen=True)
class _DocumentRow:
    id: UUID
    parsed_key: str | None
    has_citations: bool


class CitationsStage:
    name: StageName = StageName.S4

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def _load(self, run_id: UUID) -> _DocumentRow:
        citation_exists = select(Citation.id).where(Citation.document_id == Document.id).exists()
        async with self._deps.session_factory() as session:
            row = (
                await session.execute(
                    select(Document.id, Document.parsed_key, citation_exists.label("has_citations"))
                    .join(VerificationRun, VerificationRun.document_id == Document.id)
                    .where(VerificationRun.id == run_id)
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("Không tìm thấy lần chạy")
        return _DocumentRow(id=row.id, parsed_key=row.parsed_key, has_citations=row.has_citations)

    async def _targets(self, document_id: UUID) -> list[LinkTarget]:
        async with self._deps.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        ReferenceEntry.id,
                        ReferenceEntry.order_index,
                        ReferenceEntry.parsed,
                        ReferenceEntry.raw_text,
                    )
                    .where(ReferenceEntry.document_id == document_id)
                    .order_by(ReferenceEntry.order_index)
                )
            ).all()
        return [make_target(row.id, row.order_index, row.parsed, row.raw_text) for row in rows]

    async def _write(
        self, ctx: StageContext, document_id: UUID, parsed: ParsedDocument, drafts: Drafts
    ) -> None:
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            await session.execute(
                select(Document.id).where(Document.id == document_id).with_for_update()
            )
            existing = await session.scalar(
                select(Citation.id).where(Citation.document_id == document_id).limit(1)
            )
            if existing is not None:
                return  # lần chạy khác của cùng tài liệu đã ghi trước (QĐ-13)
            citations: list[Citation] = []
            rows: list[CitationLink | Claim] = []
            for draft, links in drafts:
                paragraph = parsed.paragraphs[draft.paragraph_index]
                start = paragraph.char_start + draft.sentence.start + draft.marker.start
                citation = Citation(
                    id=uuid.uuid4(),
                    document_id=document_id,
                    marker_text=draft.marker.text,
                    sentence_text=draft.sentence.text,
                    page=paragraph.page,
                    char_start=start,
                    char_end=start + len(draft.marker.text),
                    section=paragraph.section,
                )
                citations.append(citation)
                rows.extend(
                    CitationLink(
                        citation_id=citation.id,
                        reference_entry_id=entry_id,
                        link_method=draft.marker.kind.value,
                    )
                    for entry_id in links
                )
                rows.append(
                    Claim(
                        document_id=document_id,
                        citation_id=citation.id,
                        claim_text=draft.claim_text,
                        context_text=draft.context_text,
                        language=draft.language,
                        extraction_method=draft.extraction_method,
                        is_claim=draft.is_claim,
                    )
                )
            session.add_all(citations)
            await session.flush()  # citations trước: links và claims tham chiếu tới chúng
            session.add_all(rows)

    async def run(self, ctx: StageContext) -> StageResult:
        document = await self._load(ctx.run_id)
        if document.has_citations:
            return StageResult()
        targets = await self._targets(document.id)
        if not targets:
            return StageResult()  # không có danh mục: S4 không có việc (spec §7.2)
        if document.parsed_key is None:
            raise InternalError("S4 chạy khi tài liệu chưa được parse.")
        data = await self._deps.storage.get(document.parsed_key)
        parsed = await asyncio.to_thread(ParsedDocument.model_validate_json, data)
        drafts = await build_drafts(parsed, ReferenceIndex(targets))
        if drafts:
            await self._write(ctx, document.id, parsed, drafts)
        return StageResult()
