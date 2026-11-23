import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, StageName
from citeproof.core.settings import Settings
from citeproof.db.models import VerificationRun
from citeproof.pipeline.demo import DemoStage, demo_registry
from citeproof.services import runs_view
from citeproof.services.documents import DemoConfig
from tests.pipeline.helpers import drain, pipeline_settings
from tests.services.helpers import new_run, new_user

SessionFactory = async_sessionmaker[AsyncSession]


async def _run(
    s: AsyncSession, settings: Settings, cfg: DemoConfig | None
) -> tuple[object, object]:
    user = await new_user(s)
    return user, await new_run(s, settings, user, config_demo=cfg)


async def _status(session_factory: SessionFactory, run_id: object) -> VerificationRun:
    async with session_factory() as s:
        return (
            await s.execute(select(VerificationRun).where(VerificationRun.id == run_id))
        ).scalar_one()


@pytest.mark.db
async def test_demo_failure_marks_run_failed_then_resume_completes(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user, run_id = await _run(s, settings, DemoConfig(step_delay_s=0, fail_at=StageName.S2))
    registry = demo_registry(session_factory)
    await drain(session_factory, registry, pipeline_settings())
    run = await _status(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None and run.error["stage"] == "S2"
    async with session_factory() as s:
        view = await runs_view.get_run_view(s, user=user, run_id=run_id)  # type: ignore[arg-type]
    assert view.error_message == "Lỗi mô phỏng ở bước 2"
    async with session_factory() as s:
        await runs_view.resume(s, settings, user=user, run_id=run_id)  # type: ignore[arg-type]
        await s.commit()
    await drain(session_factory, registry, pipeline_settings())
    assert (await _status(session_factory, run_id)).status == RunStatus.COMPLETED.value


@pytest.mark.db
async def test_demo_delay_clamped(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async with session_factory() as s:
        _, run_id = await _run(s, settings, DemoConfig(step_delay_s=99, fail_at=None))
    stage = DemoStage(StageName.S1, session_factory, sleep=fake_sleep)
    await stage.run(run_id)  # type: ignore[arg-type]
    assert slept == [5.0]


@pytest.mark.db
async def test_no_demo_key_is_noop(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    called = False

    async def fake_sleep(seconds: float) -> None:
        nonlocal called
        called = True

    async with session_factory() as s:
        _, run_id = await _run(s, settings, None)
    stage = DemoStage(StageName.S4, session_factory, sleep=fake_sleep)
    result = await stage.run(run_id)  # type: ignore[arg-type]
    assert result.warnings == () and not called
