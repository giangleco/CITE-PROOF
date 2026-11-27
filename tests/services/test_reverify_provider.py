"""Chặn kiểm chứng lại, tra lại và tải nguồn khi provider LLM đã đổi (spec §5.4)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import ParseMethod, ReferenceStatus, RefType, RunStatus
from citeproof.core.errors import ConflictError
from citeproof.core.settings import Settings
from citeproof.db.models import ReferenceCheck, ReferenceEntry, SourceFile, VerificationRun
from citeproof.llm.identity import provider_identity
from citeproof.pipeline.config import RunConfig
from citeproof.services import reverify, sources
from citeproof.storage import LocalStorage
from citeproof.web import labels
from tests.files import titled_pdf
from tests.services.helpers import new_run, new_user, run_document_id, set_run

SessionFactory = async_sessionmaker[AsyncSession]
TITLE = "Attention Is All You Need"
# Không thể trùng định danh provider hiện tại, dù môi trường test cấu hình gì.
OLD_PROVIDER = "openai_compatible:old.example"


async def _children(s: AsyncSession, run_id: uuid.UUID) -> int:
    count = await s.scalar(
        select(func.count())
        .select_from(VerificationRun)
        .where(VerificationRun.parent_run_id == run_id)
    )
    return count or 0


async def _freeze(s: AsyncSession, run_id: uuid.UUID, provider: str | None) -> None:
    run = await s.get(VerificationRun, run_id)
    assert run is not None
    run.config = {**run.config, "provider": provider}
    await s.commit()


@pytest.mark.db
async def test_new_run_freezes_current_provider(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(s, settings, user)
        run = await s.get(VerificationRun, run_id)
    assert run is not None
    assert RunConfig.load(run.config).provider == provider_identity(settings)
    assert "provider" in run.config


@pytest.mark.db
async def test_reverify_and_lookup_retry_blocked_when_provider_changed(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
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
        s.add(
            ReferenceCheck(
                run_id=run_id,
                reference_entry_id=entry.id,
                status=ReferenceStatus.LOOKUP_FAILED.value,
                sources_searched=[],
            )
        )
        await s.commit()
        await set_run(s, run_id, status=RunStatus.COMPLETED)
        await sources.upload_source(
            s,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entry.id,
            data=titled_pdf(TITLE),
        )
        await s.commit()
        await _freeze(s, run_id, OLD_PROVIDER)

        for start in (reverify.start_reverify_uploads, reverify.start_lookup_retry):
            with pytest.raises(ConflictError) as exc:
                await start(s, settings, user=user, run_id=run_id)
            assert exc.value.code == "LLM_PROVIDER_CHANGED"
            assert str(exc.value) == labels.LLM_PROVIDER_CHANGED
            await s.rollback()
            await s.refresh(user)
            assert await _children(s, run_id) == 0

        # Cùng lần chạy nhưng không gắn provider: tạo được lần chạy con như cũ.
        await _freeze(s, run_id, None)
        await reverify.start_lookup_retry(s, settings, user=user, run_id=run_id)
        await s.commit()
        assert await _children(s, run_id) == 1


@pytest.mark.db
async def test_upload_missing_blocked_when_provider_changed(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
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
        await s.commit()
        await set_run(s, run_id, status=RunStatus.COMPLETED)
        await _freeze(s, run_id, OLD_PROVIDER)
        with pytest.raises(ConflictError) as exc:
            await sources.upload_source(
                s,
                LocalStorage(settings.storage_dir),
                settings,
                user=user,
                run_id=run_id,
                reference_entry_id=entry.id,
                data=titled_pdf(TITLE),
            )
        assert exc.value.code == "LLM_PROVIDER_CHANGED"
        await s.rollback()
        assert await s.scalar(select(func.count()).select_from(SourceFile)) == 0
