import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, RunStatus, StageName
from citeproof.core.errors import NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import Job, LLMCall, VerificationRun
from citeproof.services import runs_view
from citeproof.services.documents import DemoConfig
from tests.services.helpers import new_run, new_user, set_jobs, set_run

SessionFactory = async_sessionmaker[AsyncSession]
S = StageName


@pytest.mark.db
async def test_steps_derived_from_jobs(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(s, settings, user)
        await set_jobs(s, run_id, {S.S1: JobStatus.DONE, S.S2: JobStatus.RUNNING})
        view = await runs_view.get_run_view(s, user=user, run_id=run_id)
    assert [step.state for step in view.steps] == ["done", "running"] + ["pending"] * 7
    assert view.is_active and not view.references_ready
    assert view.document_name == "bai.pdf"


@pytest.mark.db
async def test_references_ready_after_s3(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(s, settings, user)
        await set_jobs(
            s, run_id, {S.S1: JobStatus.DONE, S.S2: JobStatus.DONE, S.S3: JobStatus.DONE}
        )
        view = await runs_view.get_run_view(s, user=user, run_id=run_id)
    assert view.references_ready


@pytest.mark.db
async def test_failed_run_message_and_stage(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(s, settings, user)
        await set_run(
            s,
            run_id,
            status=RunStatus.FAILED,
            error={"stage": "S5", "type": "InputError", "message": "Lỗi mô phỏng ở bước 5"},
        )
        view = await runs_view.get_run_view(s, user=user, run_id=run_id)
        assert view.failed_stage == S.S5 and view.error_message == "Lỗi mô phỏng ở bước 5"
        assert not view.is_active
        await set_run(s, run_id, error={"stage": "S5", "type": "KeyError", "message": "'x'"})
        view = await runs_view.get_run_view(s, user=user, run_id=run_id)
    assert view.error_message == "Lỗi hệ thống ở bước này."


@pytest.mark.db
async def test_other_users_run_not_found(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        owner = await new_user(s)
        other = await new_user(s, "o@b.vn")
        run_id = await new_run(s, settings, owner)
        with pytest.raises(NotFoundError):
            await runs_view.get_run_view(s, user=other, run_id=run_id)
        with pytest.raises(NotFoundError):
            await runs_view.get_tech_details(s, user=other, run_id=run_id)


@pytest.mark.db
async def test_resume_clears_demo_fail_at_local(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(
            s, settings, user, config_demo=DemoConfig(step_delay_s=0, fail_at=S.S1)
        )
        await set_jobs(s, run_id, {S.S1: JobStatus.FAILED})
        await set_run(s, run_id, status=RunStatus.FAILED)
        await runs_view.resume(s, settings, user=user, run_id=run_id)
        await s.commit()
        run = (
            await s.execute(select(VerificationRun).where(VerificationRun.id == run_id))
        ).scalar_one()
        job = (await s.execute(select(Job).where(Job.run_id == run_id))).scalar_one()
    assert run.config["demo"]["fail_at"] is None
    assert run.status == RunStatus.RUNNING.value and job.status == JobStatus.QUEUED.value


@pytest.mark.db
async def test_tech_details_sums_llm_calls(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(s, settings, user)
        for tokens in (100, 50):
            s.add(
                LLMCall(
                    run_id=run_id,
                    task="W5",
                    provider="scripted",
                    model="m-1",
                    prompt_version="judge/v1",
                    input_tokens=tokens,
                    output_tokens=10,
                    latency_ms=5,
                    cache_namespace="doc:x",
                )
            )
        await s.commit()
        second = await new_run(s, settings, user)
        details = await runs_view.get_tech_details(s, user=user, run_id=run_id)
    assert details.llm_calls == 2 and details.input_tokens == 150 and details.output_tokens == 20
    assert details.models == ["m-1"] and details.prompt_versions == ["judge/v1"]
    assert details.config == {"variant": "V2"}
    assert second not in [r[0] for r in details.other_runs]  # tài liệu khác
