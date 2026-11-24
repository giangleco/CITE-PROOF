"""DemoStage: lớp bọc quanh stage thật cho môi trường local (QĐ-10)."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, StageName
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.db.models import User, VerificationRun
from citeproof.pipeline.demo import DemoStage
from citeproof.pipeline.registry import build_registry
from citeproof.pipeline.stages import StageResult
from citeproof.services import documents, runs_view
from citeproof.services.documents import DemoConfig
from citeproof.storage import LocalStorage
from tests.pipeline.helpers import (
    CountingStage,
    drain,
    make_test_deps,
    pipeline_settings,
    stage_context,
)
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def _run(
    session_factory: SessionFactory, settings: Settings, cfg: DemoConfig | None
) -> tuple[User, uuid.UUID]:
    # Tài liệu văn bản: S1 thật parse được (PDF chờ Task 6).
    async with session_factory() as s:
        user = await new_user(s)
        _, run_id = await documents.create_text_document(
            s,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            title=None,
            body="Mô hình đạt độ chính xác cao hơn [1].",
            references="[1] Nguyễn Văn An (2020), Phân loại văn bản tiếng Việt.",
            demo=cfg,
        )
        await s.commit()
        return user, run_id


async def _status(session_factory: SessionFactory, run_id: uuid.UUID) -> VerificationRun:
    async with session_factory() as s:
        return (
            await s.execute(select(VerificationRun).where(VerificationRun.id == run_id))
        ).scalar_one()


@pytest.mark.db
async def test_demo_wrapper_delays_then_delegates(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, run_id = await _run(session_factory, settings, DemoConfig(step_delay_s=1.5, fail_at=None))
    sleep = FakeSleep()
    inner = CountingStage(StageName.S3, warnings=["LLM_NOT_CONFIGURED"])
    stage = DemoStage(inner, session_factory, sleep=sleep)
    assert stage.name is StageName.S3
    ctx = stage_context(run_id, stage=StageName.S3)
    result = await stage.run(ctx)
    assert sleep.calls == [1.5]
    assert inner.calls == [ctx]
    assert result == StageResult(warnings=("LLM_NOT_CONFIGURED",))


@pytest.mark.db
async def test_demo_delay_clamped(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, run_id = await _run(session_factory, settings, DemoConfig(step_delay_s=99, fail_at=None))
    sleep = FakeSleep()
    inner = CountingStage(StageName.S1)
    await DemoStage(inner, session_factory, sleep=sleep).run(stage_context(run_id))
    assert sleep.calls == [5.0]
    assert len(inner.calls) == 1


@pytest.mark.db
async def test_demo_fail_at_skips_inner(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, run_id = await _run(
        session_factory, settings, DemoConfig(step_delay_s=0, fail_at=StageName.S5)
    )
    sleep = FakeSleep()
    inner = CountingStage(StageName.S5)
    stage = DemoStage(inner, session_factory, sleep=sleep)
    with pytest.raises(InputError) as excinfo:
        await stage.run(stage_context(run_id, stage=StageName.S5))
    assert str(excinfo.value) == "Lỗi mô phỏng ở bước 5"
    assert inner.calls == []
    assert sleep.calls == []


@pytest.mark.db
async def test_demo_fail_at_other_stage_delegates(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, run_id = await _run(
        session_factory, settings, DemoConfig(step_delay_s=0, fail_at=StageName.S5)
    )
    inner = CountingStage(StageName.S4)
    await DemoStage(inner, session_factory, sleep=FakeSleep()).run(
        stage_context(run_id, stage=StageName.S4)
    )
    assert len(inner.calls) == 1


@pytest.mark.db
async def test_no_demo_key_delegates_without_sleep(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, run_id = await _run(session_factory, settings, None)
    sleep = FakeSleep()
    inner = CountingStage(StageName.S4, warnings=["X"])
    result = await DemoStage(inner, session_factory, sleep=sleep).run(
        stage_context(run_id, stage=StageName.S4)
    )
    assert result.warnings == ("X",)
    assert len(inner.calls) == 1
    assert sleep.calls == []


@pytest.mark.db
async def test_demo_failure_marks_run_failed_then_resume_completes(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    user, run_id = await _run(
        session_factory, settings, DemoConfig(step_delay_s=0, fail_at=StageName.S2)
    )
    registry = build_registry(make_test_deps(settings, session_factory))
    await drain(session_factory, registry, pipeline_settings())
    run = await _status(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None and run.error["stage"] == "S2"
    async with session_factory() as s:
        view = await runs_view.get_run_view(s, user=user, run_id=run_id)
    assert view.error_message == "Lỗi mô phỏng ở bước 2"
    async with session_factory() as s:
        await runs_view.resume(s, settings, user=user, run_id=run_id)
        await s.commit()
    await drain(session_factory, registry, pipeline_settings())
    assert (await _status(session_factory, run_id)).status == RunStatus.COMPLETED.value
