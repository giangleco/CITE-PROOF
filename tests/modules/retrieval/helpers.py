"""Dựng nguồn, chunk, claim cho test truy xuất."""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    AccessStatus,
    ChunkRegion,
    ContentLevel,
    SourceOrigin,
    TitleMatch,
)
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    SourceChunk,
    SourceFile,
    VerificationRun,
)

SessionFactory = async_sessionmaker[AsyncSession]


async def add_source(
    session_factory: SessionFactory,
    chunks: Sequence[str | tuple[str, str | None]],
    *,
    work_id: uuid.UUID | None = None,
    level: ContentLevel = ContentLevel.FULL_TEXT,
    owner_user_id: uuid.UUID | None = None,
    reference_entry_id: uuid.UUID | None = None,
    language: str = "en",
) -> uuid.UUID:
    """Một `source_files` (bản mở dùng chung, hoặc tải lên khi có chủ sở hữu) và các chunk."""
    upload = owner_user_id is not None
    key = uuid.uuid4().hex
    async with session_factory() as s:
        source = SourceFile(
            work_id=work_id,
            reference_entry_id=reference_entry_id,
            owner_user_id=owner_user_id,
            origin=(SourceOrigin.USER_UPLOAD if upload else SourceOrigin.OPEN_ACCESS).value,
            access_status=(
                AccessStatus.USER_PROVIDED
                if upload
                else (
                    AccessStatus.OPEN_ABSTRACT
                    if level is ContentLevel.ABSTRACT
                    else AccessStatus.OPEN_FULL_TEXT
                )
            ).value,
            content_level=level.value,
            storage_key=f"sources/open/{key}.txt",
            sha256=hashlib.sha256(key.encode()).hexdigest(),
            title_match=TitleMatch.UNKNOWN.value,
        )
        s.add(source)
        await s.flush()
        offset = 0
        for index, item in enumerate(chunks):
            text, section = (item, None) if isinstance(item, str) else item
            s.add(
                SourceChunk(
                    source_file_id=source.id,
                    chunk_index=index,
                    text=text,
                    page=1,
                    section=section,
                    char_start=offset,
                    char_end=offset + len(text),
                    region=ChunkRegion.BODY.value,
                    language=language,
                )
            )
            offset += len(text) + 1
        await s.commit()
        return source.id


@dataclass(frozen=True)
class ClaimSpec:
    text: str
    language: str = "vi"
    links: tuple[uuid.UUID | None, ...] = ()
    is_claim: bool = True


async def add_claims(
    session_factory: SessionFactory, run_id: uuid.UUID, claims: Sequence[ClaimSpec]
) -> list[uuid.UUID]:
    """Citation, link và claim cho mỗi mục, theo thứ tự trong bài; trả về id các claim."""
    async with session_factory() as s:
        document_id = await s.scalar(
            select(VerificationRun.document_id).where(VerificationRun.id == run_id)
        )
        assert document_id is not None
        ids: list[uuid.UUID] = []
        for index, spec in enumerate(claims):
            citation = Citation(
                document_id=document_id,
                marker_text="[1]",
                sentence_text=f"{spec.text} [1].",
                page=1,
                char_start=index * 1000,
                char_end=index * 1000 + 3,
                section=None,
            )
            s.add(citation)
            await s.flush()
            s.add_all(
                CitationLink(citation_id=citation.id, reference_entry_id=ref, link_method="numeric")
                for ref in spec.links
            )
            claim = Claim(
                document_id=document_id,
                citation_id=citation.id,
                claim_text=spec.text,
                context_text="",
                language=spec.language,
                extraction_method="rule",
                is_claim=spec.is_claim,
            )
            s.add(claim)
            await s.flush()
            ids.append(claim.id)
        await s.commit()
    return ids
