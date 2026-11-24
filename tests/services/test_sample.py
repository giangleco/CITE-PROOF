from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ClaimStatus,
    EvidenceRelation,
    JobStatus,
    ReferenceStatus,
    RunStatus,
    SourceOrigin,
)
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Citation,
    Claim,
    ClaimVerification,
    Document,
    Evidence,
    Job,
    ReferenceCheck,
    SourceChunk,
    SourceFile,
    VerificationRun,
    Work,
)
from citeproof.services.documents import is_sample
from citeproof.services.sample import seed_sample
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]
VASWANI_DOI = "10.48550/arxiv.1706.03762"  # dòng thật lưu DOI chuẩn hóa


@pytest.mark.db
async def test_seed_sample_counts(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        doc_id, run_id = await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        doc = await s.get(Document, doc_id)
        run = await s.get(VerificationRun, run_id)
        refs = Counter((await s.execute(select(ReferenceCheck.status))).scalars())
        claims = Counter((await s.execute(select(ClaimVerification.status))).scalars())
        jobs = (await s.execute(select(Job.status).where(Job.run_id == run_id))).scalars().all()
    assert doc is not None and is_sample(doc) and doc.user_id == user.id
    assert run is not None and run.status == RunStatus.COMPLETED.value
    assert set(refs) == {s.value for s in ReferenceStatus}
    assert refs[ReferenceStatus.MATCHED.value] == 2 and sum(refs.values()) == 7
    assert set(claims) == {s.value for s in ClaimStatus}
    assert claims[ClaimStatus.UNVERIFIABLE.value] == 2 and sum(claims.values()) == 6
    assert jobs == [JobStatus.DONE.value] * 9


_MINIMUM = {
    ClaimStatus.SUPPORTED.value: {EvidenceRelation.SUPPORTS.value},
    ClaimStatus.PARTIALLY_SUPPORTED.value: {
        EvidenceRelation.SUPPORTS.value,
        EvidenceRelation.PARTIALLY_SUPPORTS.value,
    },
    ClaimStatus.CONTRADICTED.value: {EvidenceRelation.CONTRADICTS.value},
    ClaimStatus.UNSUPPORTED.value: {EvidenceRelation.RELATED.value},
}


@pytest.mark.db
async def test_sample_evidence_minimums(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        rows = (
            await s.execute(
                select(ClaimVerification.status, Evidence, SourceChunk)
                .join(Evidence, Evidence.claim_verification_id == ClaimVerification.id)
                .join(SourceChunk, SourceChunk.id == Evidence.source_chunk_id)
            )
        ).all()
    relations: dict[str, set[str]] = {}
    for status, evidence, chunk in rows:
        relations.setdefault(status, set()).add(evidence.relation)
        assert chunk.text[evidence.char_start : evidence.char_end] == evidence.quote_text
    for status, allowed in _MINIMUM.items():
        assert relations.get(status, set()) & allowed, status


@pytest.mark.db
async def test_sample_does_not_touch_shared_sources(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        files = (await s.execute(select(SourceFile))).scalars().all()
        works = (await s.execute(select(Work.metadata_source))).scalars().all()
    assert files and all(f.origin == SourceOrigin.USER_UPLOAD.value for f in files)
    assert all(f.owner_user_id == user.id for f in files)
    assert set(works) == {"sample"}


@pytest.mark.db
async def test_sample_reuses_existing_work(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    from datetime import UTC, datetime

    async with session_factory() as s:
        s.add(
            Work(
                doi=VASWANI_DOI,
                title="Attention Is All You Need",
                title_norm="attention is all you need",
                metadata_source="crossref",
                retrieved_at=datetime.now(UTC),
            )
        )
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        rows = (await s.execute(select(Work).where(Work.doi == VASWANI_DOI))).scalars().all()
    assert len(rows) == 1 and rows[0].metadata_source == "crossref"


@pytest.mark.db
async def test_seed_twice_reuses_sample_works(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        docs = (await s.execute(select(Document))).scalars().all()
    assert len(docs) == 2


@pytest.mark.db
async def test_seed_sample_local_only(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    pilot = settings.model_copy(update={"env": "pilot"})
    async with session_factory() as s:
        user = await new_user(s)
        with pytest.raises(InputError) as exc:
            await seed_sample(s, pilot, user_id=user.id)
    assert exc.value.code == "SAMPLE_LOCAL_ONLY"


@pytest.mark.db
async def test_claim_text_is_substring_of_sentence(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        rows = (
            await s.execute(
                select(Claim.claim_text, Citation.sentence_text).join(
                    Citation, Citation.id == Claim.citation_id
                )
            )
        ).all()
    assert len(rows) == 6
    for claim_text, sentence in rows:
        assert claim_text in sentence
