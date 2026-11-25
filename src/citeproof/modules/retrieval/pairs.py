"""Cặp claim-reference của lần chạy và chọn nguồn cho cặp (spec §7.7; dùng chung S7, S8, V3).

`choose_sources` dùng `sources.selection.entry_works` để biết work của reference trong lần
chạy (hoặc lần chạy cha), nên nguồn được chọn khớp với nguồn S6 đã lập chỉ mục.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import ContentLevel, SourceOrigin, TitleMatch
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    ReferenceEntry,
    SourceChunk,
    SourceFile,
)
from citeproof.modules.sources.selection import entry_works
from citeproof.pipeline.config import RunInfo

__all__ = [
    "ClaimPair",
    "SourceChoice",
    "SourceRef",
    "choose_sources",
    "load_pairs",
]


@dataclass(frozen=True)
class ClaimPair:
    claim_id: UUID
    citation_id: UUID
    reference_entry_id: UUID | None
    claim_text: str
    context_text: str
    sentence_text: str
    language: str
    is_claim: bool
    position: int


@dataclass(frozen=True)
class SourceRef:
    source_file_id: UUID
    content_level: ContentLevel
    origin: SourceOrigin
    title_match: TitleMatch
    chunk_count: int
    language: str | None


@dataclass(frozen=True)
class SourceChoice:
    full_text: SourceRef | None
    abstract: SourceRef | None


async def load_pairs(session: AsyncSession, info: RunInfo) -> list[ClaimPair]:
    """Mỗi `citation_links` của tài liệu (kể cả link NULL) thành một cặp, theo vị trí trong bài.

    Lần chạy con chỉ giữ cặp có reference thuộc phạm vi (`RunInfo.in_scope`).
    """
    rows = (
        await session.execute(
            select(
                Claim.id,
                Claim.citation_id,
                CitationLink.reference_entry_id,
                Claim.claim_text,
                Claim.context_text,
                Citation.sentence_text,
                Claim.language,
                Claim.is_claim,
            )
            .join(Citation, Citation.id == Claim.citation_id)
            .join(CitationLink, CitationLink.citation_id == Citation.id)
            .outerjoin(ReferenceEntry, ReferenceEntry.id == CitationLink.reference_entry_id)
            .where(Claim.document_id == info.document_id)
            .order_by(
                Citation.char_start,
                ReferenceEntry.order_index.nulls_last(),
                CitationLink.id,
            )
        )
    ).all()
    kept = [row for row in rows if info.in_scope(row.reference_entry_id)]
    return [
        ClaimPair(
            claim_id=row.id,
            citation_id=row.citation_id,
            reference_entry_id=row.reference_entry_id,
            claim_text=row.claim_text,
            context_text=row.context_text,
            sentence_text=row.sentence_text,
            language=row.language,
            is_claim=row.is_claim,
            position=position,
        )
        for position, row in enumerate(kept)
    ]


async def _source_ref(session: AsyncSession, source: SourceFile) -> SourceRef:
    counts = (
        await session.execute(
            select(SourceChunk.language, func.count().label("n"))
            .where(SourceChunk.source_file_id == source.id)
            .group_by(SourceChunk.language)
            .order_by(func.count().desc(), SourceChunk.language)
        )
    ).all()
    return SourceRef(
        source_file_id=source.id,
        content_level=ContentLevel(source.content_level),
        origin=SourceOrigin(source.origin),
        title_match=TitleMatch(source.title_match),
        chunk_count=sum(row.n for row in counts),
        language=counts[0].language if counts else None,
    )


async def choose_sources(
    session: AsyncSession, info: RunInfo, reference_entry_id: UUID | None
) -> SourceChoice:
    """Nguồn toàn văn và abstract của một reference.

    Toàn văn: `USER_UPLOAD` mới nhất của chính người dùng cho reference này, nếu không có thì
    `OPEN_ACCESS` FULL_TEXT mới nhất của work đã khớp (trong lần chạy này hoặc lần chạy cha).
    Abstract: `OPEN_ACCESS` ABSTRACT mới nhất của work.
    """
    if reference_entry_id is None:
        return SourceChoice(None, None)
    full: SourceFile | None = await session.scalar(
        select(SourceFile)
        .where(
            SourceFile.origin == SourceOrigin.USER_UPLOAD.value,
            SourceFile.owner_user_id == info.user_id,
            SourceFile.reference_entry_id == reference_entry_id,
            SourceFile.content_level == ContentLevel.FULL_TEXT.value,
        )
        .order_by(SourceFile.created_at.desc(), SourceFile.id)
        .limit(1)
    )
    work_id = dict(
        await entry_works(session, info, reference_entry_id=reference_entry_id, scoped=False)
    ).get(reference_entry_id)
    abstract: SourceFile | None = None
    if work_id is not None:
        for level in (ContentLevel.FULL_TEXT, ContentLevel.ABSTRACT):
            if level is ContentLevel.FULL_TEXT and full is not None:
                continue
            found: SourceFile | None = await session.scalar(
                select(SourceFile)
                .where(
                    SourceFile.origin == SourceOrigin.OPEN_ACCESS.value,
                    SourceFile.work_id == work_id,
                    SourceFile.content_level == level.value,
                )
                .order_by(SourceFile.created_at.desc(), SourceFile.id)
                .limit(1)
            )
            if level is ContentLevel.FULL_TEXT:
                full = found
            else:
                abstract = found
    return SourceChoice(
        full_text=await _source_ref(session, full) if full is not None else None,
        abstract=await _source_ref(session, abstract) if abstract is not None else None,
    )
