"""Tạo lần chạy con: kiểm chứng lại sau khi tải nguồn, tra lại tài liệu lỗi kết nối (T-505)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ParseMethod,
    ReferenceStatus,
    RefType,
    RunStatus,
    StageName,
    UnverifiableReason,
)
from citeproof.core.errors import ConflictError, InputError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import ReferenceCheck, ReferenceEntry, UsageEvent, User, VerificationRun
from citeproof.pipeline.config import RunConfig
from citeproof.services import reverify, sources
from citeproof.storage import LocalStorage
from tests.files import titled_pdf
from tests.services.helpers import (
    add_claim_verification,
    new_run,
    new_user,
    run_document_id,
    set_run,
)

SessionFactory = async_sessionmaker[AsyncSession]
TITLE = "Attention Is All You Need"


async def _finished_run(
    s: AsyncSession, settings: Settings, *, email: str = "u@b.vn"
) -> tuple[User, uuid.UUID, uuid.UUID]:
    """Lần chạy đã xong có một tài liệu [1] chờ toàn văn."""
    user = await new_user(s, email)
    run_id = await new_run(s, settings, user)
    entry = ReferenceEntry(
        document_id=await run_document_id(s, run_id),
        order_index=0,
        raw_text="[1] Vaswani et al.",
        parsed={"title": TITLE},
        ref_type=RefType.ARTICLE.value,
        parse_method=ParseMethod.RULE.value,
    )
    s.add(entry)
    await s.flush()
    await add_claim_verification(
        s,
        run_id,
        entry_id=entry.id,
        sentence="Một nhận định chờ toàn văn [1].",
        reason=UnverifiableReason.NO_ACCESSIBLE_TEXT,
    )
    await s.commit()
    await set_run(s, run_id, status=RunStatus.COMPLETED)
    return user, run_id, entry.id


async def _upload(
    s: AsyncSession, settings: Settings, user: User, run_id: uuid.UUID, entry_id: uuid.UUID
) -> None:
    await sources.upload_source(
        s,
        LocalStorage(settings.storage_dir),
        settings,
        user=user,
        run_id=run_id,
        reference_entry_id=entry_id,
        data=titled_pdf(TITLE),
    )
    await s.commit()


@pytest.mark.db
async def test_reverify_creates_child_run_with_scope(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _finished_run(s, settings)
        await _upload(s, settings, user, run_id, entry_id)
        parent = await s.get(VerificationRun, run_id)
        assert parent is not None
        parent_config = RunConfig.load(parent.config)
        child_id = await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        await s.commit()
        child = await s.get(VerificationRun, child_id)
    assert child is not None
    assert child.parent_run_id == run_id
    config = RunConfig.load(child.config)
    assert config.scope is not None
    assert config.scope.kind == "upload"
    assert config.scope.reference_entry_ids == [entry_id]
    assert config.scope.from_stage is StageName.S6
    assert config.model_copy(update={"scope": None}) == parent_config


@pytest.mark.db
async def test_partial_run_conflict_when_active(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _finished_run(s, settings)
        await _upload(s, settings, user, run_id, entry_id)
        await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        await s.commit()
        with pytest.raises(ConflictError) as exc:
            await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        assert exc.value.code == "RUN_IN_PROGRESS"
        await s.rollback()
        await s.refresh(user)
        with pytest.raises(ConflictError) as exc:
            await reverify.start_lookup_retry(s, settings, user=user, run_id=run_id)
        assert exc.value.code == "RUN_IN_PROGRESS"


@pytest.mark.db
async def test_reverify_requires_uploads(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, _ = await _finished_run(s, settings)
        with pytest.raises(InputError) as exc:
            await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        assert exc.value.code == "NO_NEW_UPLOADS"


@pytest.mark.db
async def test_reverify_ignores_uploads_older_than_run(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _finished_run(s, settings)
        await _upload(s, settings, user, run_id, entry_id)
        child_id = await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        await s.commit()
        # Lần chạy con đã dùng bản tải lên; kiểm chứng lại từ lần chạy con không còn gì mới.
        await set_run(s, child_id, status=RunStatus.COMPLETED)
        await add_claim_verification(
            s,
            child_id,
            entry_id=entry_id,
            sentence="Nhận định vẫn chờ toàn văn [1].",
            reason=UnverifiableReason.NO_ACCESSIBLE_TEXT,
        )
        await s.commit()
        with pytest.raises(InputError) as exc:
            await reverify.start_reverify_uploads(s, settings, user=user, run_id=child_id)
        assert exc.value.code == "NO_NEW_UPLOADS"


@pytest.mark.db
async def test_lookup_retry_requires_failed_lookups(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _finished_run(s, settings)
        s.add(
            ReferenceCheck(
                run_id=run_id,
                reference_entry_id=entry_id,
                status=ReferenceStatus.MATCHED.value,
                sources_searched=["crossref"],
            )
        )
        await s.commit()
        with pytest.raises(InputError) as exc:
            await reverify.start_lookup_retry(s, settings, user=user, run_id=run_id)
        assert exc.value.code == "NO_FAILED_LOOKUPS"


@pytest.mark.db
async def test_lookup_retry_scope_and_no_usage(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _finished_run(s, settings)
        s.add(
            ReferenceCheck(
                run_id=run_id,
                reference_entry_id=entry_id,
                status=ReferenceStatus.LOOKUP_FAILED.value,
            )
        )
        await s.commit()
        before = await s.scalar(select(func.count()).select_from(UsageEvent))
        child_id = await reverify.start_lookup_retry(s, settings, user=user, run_id=run_id)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(UsageEvent)) == before
        child = await s.get(VerificationRun, child_id)
    assert child is not None and child.parent_run_id == run_id
    scope = RunConfig.load(child.config).scope
    assert scope is not None
    assert (scope.kind, scope.reference_entry_ids, scope.from_stage) == (
        "lookup_retry",
        [entry_id],
        StageName.S3,
    )


@pytest.mark.db
async def test_partial_run_needs_finished_parent(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id, entry_id = await _finished_run(s, settings)
        await _upload(s, settings, user, run_id, entry_id)
        await set_run(s, run_id, status=RunStatus.FAILED)
        with pytest.raises(InputError) as exc:
            await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        assert exc.value.code == "RUN_NOT_FINISHED"


@pytest.mark.db
async def test_other_users_run_is_not_found(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        owner, run_id, entry_id = await _finished_run(s, settings)
        await _upload(s, settings, owner, run_id, entry_id)
        other = await new_user(s, "khac@b.vn")
        with pytest.raises(NotFoundError):
            await reverify.start_reverify_uploads(s, settings, user=other, run_id=run_id)
        with pytest.raises(NotFoundError):
            await reverify.start_lookup_retry(s, settings, user=other, run_id=run_id)
