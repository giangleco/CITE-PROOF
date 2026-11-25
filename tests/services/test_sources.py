import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    AccessStatus,
    ContentLevel,
    ParseMethod,
    RefType,
    SourceOrigin,
    TitleMatch,
    UserRole,
)
from citeproof.core.errors import InputError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import ReferenceEntry, SourceFile, User
from citeproof.services import deletion, sources
from citeproof.storage import LocalStorage
from tests.files import PDF_BYTES, docx_bytes, titled_pdf
from tests.services.helpers import new_run, new_user, run_document_id

SessionFactory = async_sessionmaker[AsyncSession]
TITLE = "Attention Is All You Need"


async def _setup(
    s: AsyncSession, settings: Settings, *, email: str = "u@b.vn", title: str | None = TITLE
) -> tuple[User, uuid.UUID, uuid.UUID]:
    user = await new_user(s, email)
    run_id = await new_run(s, settings, user)
    entry = ReferenceEntry(
        document_id=await run_document_id(s, run_id),
        order_index=0,
        raw_text="[1] Vaswani et al.",
        parsed={"title": title} if title else {},
        ref_type=RefType.ARTICLE.value,
        parse_method=ParseMethod.RULE.value,
    )
    s.add(entry)
    await s.commit()
    return user, run_id, entry.id


@pytest.mark.db
async def test_upload_creates_user_upload_source(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        data = titled_pdf(TITLE)
        uploaded = await sources.upload_source(
            s, storage, settings, user=user, run_id=run_id, reference_entry_id=entry_id, data=data
        )
        await s.commit()
        row = await s.get(SourceFile, uploaded.source_file_id)
        assert row is not None
        assert row.origin == SourceOrigin.USER_UPLOAD.value
        assert row.owner_user_id == user.id
        assert row.reference_entry_id == entry_id
        assert row.access_status == AccessStatus.USER_PROVIDED.value
        assert row.content_level == ContentLevel.FULL_TEXT.value
        assert row.title_match == TitleMatch.MATCH.value == uploaded.title_match.value
        document_id = await run_document_id(s, run_id)
        assert row.storage_key == (
            f"documents/{user.id}/{document_id}/sources/{uploaded.source_file_id}.pdf"
        )
        assert await storage.get(row.storage_key) == data
        found = await sources.uploads_for_run(s, user=user, run_id=run_id)
        assert found[entry_id].source_file_id == uploaded.source_file_id


@pytest.mark.db
async def test_upload_mismatch_and_unknown(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        other = await sources.upload_source(
            s,
            storage,
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entry_id,
            data=titled_pdf("BERT: Pre-training of Deep Bidirectional Transformers"),
        )
        assert other.title_match is TitleMatch.MISMATCH
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings, email="v@b.vn", title=None)
        unknown = await sources.upload_source(
            s,
            storage,
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entry_id,
            data=titled_pdf(TITLE),
        )
        assert unknown.title_match is TitleMatch.UNKNOWN


@pytest.mark.db
async def test_upload_non_pdf_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        with pytest.raises(InputError) as info:
            await sources.upload_source(
                s,
                LocalStorage(settings.storage_dir),
                settings,
                user=user,
                run_id=run_id,
                reference_entry_id=entry_id,
                data=docx_bytes(),
            )
        assert info.value.code == "UNSUPPORTED_FILE_TYPE"


@pytest.mark.db
async def test_upload_too_large(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    small = settings.model_copy(update={"max_upload_bytes": 100})
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        with pytest.raises(InputError) as info:
            await sources.upload_source(
                s,
                LocalStorage(settings.storage_dir),
                small,
                user=user,
                run_id=run_id,
                reference_entry_id=entry_id,
                data=titled_pdf(TITLE),
            )
        assert info.value.code == "FILE_TOO_LARGE"


@pytest.mark.db
async def test_upload_page_limit(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    small = settings.model_copy(update={"max_pages": 2})
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        with pytest.raises(InputError) as info:
            await sources.upload_source(
                s,
                LocalStorage(settings.storage_dir),
                small,
                user=user,
                run_id=run_id,
                reference_entry_id=entry_id,
                data=titled_pdf(TITLE, pages=3),
            )
        assert info.value.code == "PAGE_LIMIT_EXCEEDED"


@pytest.mark.db
async def test_upload_other_users_run_404(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        _, run_id, entry_id = await _setup(s, settings)
        intruder = await new_user(s, "x@b.vn", UserRole.USER)
        with pytest.raises(NotFoundError):
            await sources.upload_source(
                s,
                LocalStorage(settings.storage_dir),
                settings,
                user=intruder,
                run_id=run_id,
                reference_entry_id=entry_id,
                data=titled_pdf(TITLE),
            )
        with pytest.raises(NotFoundError):
            await sources.uploads_for_run(s, user=intruder, run_id=run_id)


@pytest.mark.db
async def test_upload_reference_not_in_run_document_404(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, _ = await _setup(s, settings)
        other_run = await new_run(s, settings, user)
        foreign = ReferenceEntry(
            document_id=await run_document_id(s, other_run),
            order_index=0,
            raw_text="[1] khác",
            parsed={},
            ref_type=RefType.ARTICLE.value,
            parse_method=ParseMethod.RULE.value,
        )
        s.add(foreign)
        await s.commit()
        for entry_id in (foreign.id, uuid.uuid4()):
            with pytest.raises(NotFoundError):
                await sources.upload_source(
                    s,
                    LocalStorage(settings.storage_dir),
                    settings,
                    user=user,
                    run_id=run_id,
                    reference_entry_id=entry_id,
                    data=PDF_BYTES,
                )


@pytest.mark.db
async def test_discard_deletes_row_and_file(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        up = await sources.upload_source(
            s,
            storage,
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entry_id,
            data=titled_pdf(TITLE),
        )
        await s.commit()
        key = (await s.get(SourceFile, up.source_file_id)).storage_key  # type: ignore[union-attr]
        intruder = await new_user(s, "x@b.vn")
        with pytest.raises(NotFoundError):
            await sources.discard_source(
                s, storage, user=intruder, run_id=run_id, source_file_id=up.source_file_id
            )
        await sources.discard_source(
            s, storage, user=user, run_id=run_id, source_file_id=up.source_file_id
        )
        assert await s.get(SourceFile, up.source_file_id) is None
        assert not (Path(settings.storage_dir) / key).exists()
        with pytest.raises(NotFoundError):
            await sources.discard_source(
                s, storage, user=user, run_id=run_id, source_file_id=up.source_file_id
            )


@pytest.mark.db
async def test_deleting_document_removes_uploads(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        up = await sources.upload_source(
            s,
            storage,
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entry_id,
            data=titled_pdf(TITLE),
        )
        await s.commit()
        key = (await s.get(SourceFile, up.source_file_id)).storage_key  # type: ignore[union-attr]
        document_id = await run_document_id(s, run_id)
        await deletion.delete_document(s, storage, user=user, document_id=document_id)
        assert await s.scalar(select(func.count()).select_from(SourceFile)) == 0
        assert not (Path(settings.storage_dir) / key).exists()


@pytest.mark.db
async def test_list_missing_attaches_upload(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    from citeproof.services import report
    from citeproof.services.sample import seed_sample

    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await new_user(s)
        _, run_id = await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        _, before = await report.list_missing(s, user=user, run_id=run_id)
        assert before and all(item.uploaded is None for item in before)
        target = before[0]
        up = await sources.upload_source(
            s,
            storage,
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=target.reference_entry_id,
            data=titled_pdf("Totally different"),
        )
        await s.commit()
        _, after = await report.list_missing(s, user=user, run_id=run_id)
    by_id = {item.reference_entry_id: item for item in after}
    uploaded = by_id[target.reference_entry_id].uploaded
    assert uploaded is not None
    assert uploaded.source_file_id == up.source_file_id
    assert uploaded.title_match is TitleMatch.MISMATCH
    assert all(
        i.uploaded is None for i in after if i.reference_entry_id != target.reference_entry_id
    )


async def _add_evidence(s: AsyncSession, run_id: uuid.UUID, source_file_id: uuid.UUID) -> None:
    from citeproof.core.enums import ChunkRegion, EvidenceRelation
    from citeproof.db.models import Evidence, SourceChunk
    from tests.services.helpers import add_claim_verification

    cv_id = await add_claim_verification(s, run_id, entry_id=None, sentence="Câu [1].")
    chunk = SourceChunk(
        source_file_id=source_file_id,
        chunk_index=0,
        text="văn bản",
        page=1,
        char_start=0,
        char_end=7,
        region=ChunkRegion.BODY.value,
        language="vi",
    )
    s.add(chunk)
    await s.flush()
    s.add(
        Evidence(
            claim_verification_id=cv_id,
            source_chunk_id=chunk.id,
            relation=EvidenceRelation.SUPPORTS.value,
            quote_text="văn bản",
            char_start=0,
            char_end=7,
        )
    )
    await s.commit()


@pytest.mark.db
async def test_discard_refused_when_evidence_uses_source(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    from citeproof.core.errors import ConflictError
    from citeproof.db.models import Evidence

    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user, run_id, entry_id = await _setup(s, settings)
        up = await sources.upload_source(
            s,
            storage,
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entry_id,
            data=titled_pdf(TITLE),
        )
        await s.commit()
        await _add_evidence(s, run_id, up.source_file_id)
        with pytest.raises(ConflictError) as info:
            await sources.discard_source(
                s, storage, user=user, run_id=run_id, source_file_id=up.source_file_id
            )
        assert info.value.code == "SOURCE_IN_USE"
        assert await s.get(SourceFile, up.source_file_id) is not None
        assert await s.scalar(select(func.count()).select_from(Evidence)) == 1
