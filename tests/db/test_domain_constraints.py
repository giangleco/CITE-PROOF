import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevel,
    ContentLevelUsed,
    ReferenceStatus,
    SourceOrigin,
    TitleMatch,
    UnverifiableReason,
    Verifier,
)
from citeproof.db.models import (
    Claim,
    ClaimVerification,
    Document,
    Evidence,
    ReferenceCheck,
    ReferenceEntry,
    SourceChunk,
    SourceFile,
    VerificationRun,
)
from tests.conftest import alembic_config
from tests.db.factories import (
    make_claim,
    make_document,
    make_reference_entry,
    make_run,
    make_user,
)


def _verification(
    run: VerificationRun,
    claim_id,
    entry_id,
    *,
    status: str = ClaimStatus.SUPPORTED.value,
    reason: str | None = None,
    verifier: str = Verifier.V2.value,
) -> ClaimVerification:
    return ClaimVerification(
        run_id=run.id,
        claim_id=claim_id,
        reference_entry_id=entry_id,
        status=status,
        unverifiable_reason=reason,
        content_level_used=ContentLevelUsed.ABSTRACT.value,
        explanation="e",
        model="m",
        prompt_version="p1",
        schema_version="verdict/2",
        verifier=verifier,
    )


def _check(run: VerificationRun, entry: ReferenceEntry, **kw: object) -> ReferenceCheck:
    return ReferenceCheck(
        run_id=run.id,
        reference_entry_id=entry.id,
        status=kw.pop("status", ReferenceStatus.MATCHED.value),
        **kw,
    )


@pytest.mark.db
async def test_unverifiable_requires_reason(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(
            _verification(
                run,
                claim.id,
                entry.id,
                status=ClaimStatus.UNVERIFIABLE.value,
                reason=UnverifiableReason.NO_ACCESSIBLE_TEXT.value,
            )
        )
        await s.flush()
        s.add(
            _verification(
                run,
                claim.id,
                entry.id,
                status=ClaimStatus.UNVERIFIABLE.value,
                verifier=Verifier.V3.value,
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_not_found_requires_sources_searched(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(_check(run, entry, status=ReferenceStatus.LOOKUP_FAILED.value))
        await s.flush()  # LOOKUP_FAILED với [] hợp lệ
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(
            _check(
                run,
                entry,
                status=ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value,
                sources_searched=[],
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(
            _check(
                run,
                entry,
                status=ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value,
                sources_searched=["crossref"],
            )
        )
        await s.flush()


def _source(**kw: object) -> SourceFile:
    return SourceFile(
        access_status="OPEN",
        content_level=ContentLevel.ABSTRACT.value,
        storage_key="k",
        sha256="s",
        title_match=TitleMatch.UNKNOWN.value,
        **kw,
    )


@pytest.mark.db
async def test_user_upload_requires_owner_and_reference(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        user = await make_user(s)
        doc = await make_document(s, user)
        entry = await make_reference_entry(s, doc)
        s.add(
            _source(
                origin=SourceOrigin.USER_UPLOAD.value,
                owner_user_id=user.id,
                reference_entry_id=entry.id,
            )
        )
        s.add(_source(origin=SourceOrigin.OPEN_ACCESS.value))
        await s.flush()
        s.add(_source(origin=SourceOrigin.USER_UPLOAD.value, owner_user_id=user.id))
        with pytest.raises(IntegrityError):
            await s.flush()
    async with session_factory() as s:
        s.add(_source(origin=SourceOrigin.USER_UPLOAD.value))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_reference_check_unique_per_run_and_entry(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(_check(run, entry))
        await s.flush()
        s.add(_check(run, entry))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_claim_verification_unique_per_run_claim_verifier(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(_verification(run, claim.id, entry.id, verifier=Verifier.V2.value))
        s.add(_verification(run, claim.id, entry.id, verifier=Verifier.V2_A2.value))
        await s.flush()
        s.add(_verification(run, claim.id, entry.id, verifier=Verifier.V2.value))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_null_reference_requires_unresolved_reason(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Cặp claim-reference không có reference chỉ được ghi với `REFERENCE_UNRESOLVED` (QĐ-14)."""
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        s.add(
            _verification(
                run,
                claim.id,
                None,
                status=ClaimStatus.UNVERIFIABLE.value,
                reason=UnverifiableReason.REFERENCE_UNRESOLVED.value,
            )
        )
        await s.flush()
        s.add(_verification(run, claim.id, None, verifier=Verifier.V3.value))
        with pytest.raises(IntegrityError):
            await s.flush()
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        s.add(
            _verification(
                run,
                claim.id,
                None,
                status=ClaimStatus.UNVERIFIABLE.value,
                reason=UnverifiableReason.NO_ACCESSIBLE_TEXT.value,
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_same_claim_two_references_allowed(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Khóa (run, claim, reference, verifier) NULLS NOT DISTINCT: claim trích hai nguồn có hai
    dòng; hai dòng NULL cho cùng claim và verifier vẫn là trùng.
    """
    unresolved = {
        "status": ClaimStatus.UNVERIFIABLE.value,
        "reason": UnverifiableReason.REFERENCE_UNRESOLVED.value,
    }
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        first = await make_reference_entry(s, doc)
        second = await make_reference_entry(s, doc)
        s.add(_verification(run, claim.id, first.id))
        s.add(_verification(run, claim.id, second.id))
        s.add(_verification(run, claim.id, None, **unresolved))
        s.add(_verification(run, claim.id, None, verifier=Verifier.V3.value, **unresolved))
        await s.flush()
        s.add(_verification(run, claim.id, None, **unresolved))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_claim_is_claim_defaults_true(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        claim = await make_claim(s, doc)
        await s.commit()
        assert await s.scalar(select(Claim.is_claim).where(Claim.id == claim.id)) is True


@pytest.mark.db
async def test_invalid_enum_value_rejected(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        entry = await make_reference_entry(s, doc)
        s.add(_check(run, entry, status="FAKE"))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_delete_document_cascades(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        entry = await make_reference_entry(s, doc)
        source = _source(
            origin=SourceOrigin.USER_UPLOAD.value,
            owner_user_id=doc.user_id,
            reference_entry_id=entry.id,
        )
        s.add(source)
        await s.flush()
        chunk = SourceChunk(
            source_file_id=source.id,
            chunk_index=0,
            text="hello world",
            char_start=0,
            char_end=11,
            region="BODY",
            language="en",
        )
        verification = _verification(run, claim.id, entry.id)
        s.add_all([chunk, verification])
        await s.flush()
        s.add(
            Evidence(
                claim_verification_id=verification.id,
                source_chunk_id=chunk.id,
                relation="SUPPORTS",
                quote_text="hello",
                char_start=0,
                char_end=5,
            )
        )
        await s.commit()
        doc_id = doc.id
    async with session_factory() as s:
        await s.delete(await s.get(Document, doc_id))
        await s.commit()
        for model in (
            ReferenceEntry,
            VerificationRun,
            ClaimVerification,
            Evidence,
            SourceFile,
            Claim,
        ):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.db
def test_models_match_migrations(database_url: str) -> None:
    command.check(alembic_config(database_url))
