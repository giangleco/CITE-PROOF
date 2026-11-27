"""Provider LLM đóng băng vào lần chạy; worker chặn stage cần LLM khi provider đã đổi (§5.4)."""

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, RunStatus, StageName, Verifier
from citeproof.core.errors import ConflictError
from citeproof.core.settings import Settings
from citeproof.db.models import Job, VerificationRun
from citeproof.pipeline.config import RunConfig, ensure_same_provider, provider_changed
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.worker import LLM_STAGES, work_once
from citeproof.web import labels
from tests.db.factories import make_document
from tests.pipeline.helpers import counting_registry, pipeline_settings

S = StageName
SessionFactory = async_sessionmaker[AsyncSession]
OPENAI = "openai_compatible:localhost:11434"


def _anthropic(settings: Settings | None = None) -> Settings:
    base = settings or Settings(_env_file=None)  # type: ignore[call-arg]
    return base.model_copy(
        update={"llm_provider": "anthropic", "anthropic_api_key": SecretStr("k")}
    )


def _openai(settings: Settings | None = None) -> Settings:
    base = settings or Settings(_env_file=None)  # type: ignore[call-arg]
    return base.model_copy(
        update={
            "llm_provider": "openai_compatible",
            "openai_base_url": "http://localhost:11434/v1",
            "anthropic_api_key": None,
        }
    )


def _unconfigured() -> Settings:
    return Settings(_env_file=None, anthropic_api_key=None)  # type: ignore[call-arg]


def test_resolve_freezes_current_provider() -> None:
    assert RunConfig.resolve(_anthropic(), variant=Verifier.V2).provider == "anthropic"
    assert RunConfig.resolve(_openai(), variant=Verifier.V2).provider == OPENAI
    assert RunConfig.resolve(_unconfigured(), variant=Verifier.V2).provider is None


def test_old_config_without_provider_loads_as_none() -> None:
    assert RunConfig.load({"variant": "V2"}).provider is None
    assert RunConfig.load(None).provider is None


def test_ensure_same_provider() -> None:
    frozen = RunConfig(provider="anthropic")
    ensure_same_provider(frozen, _anthropic())
    assert not provider_changed(frozen, _anthropic())
    for settings in (_openai(), _unconfigured()):
        assert provider_changed(frozen, settings)
        with pytest.raises(ConflictError) as exc:
            ensure_same_provider(frozen, settings)
        assert exc.value.code == "LLM_PROVIDER_CHANGED"
        assert str(exc.value) == labels.LLM_PROVIDER_CHANGED
    # Lần chạy không gắn provider tiếp tục được với provider nào cũng được.
    for settings in (_anthropic(), _openai(), _unconfigured()):
        ensure_same_provider(RunConfig(), settings)
        assert not provider_changed(RunConfig(), settings)


def test_llm_stages_exclude_deterministic_stages() -> None:
    assert frozenset({S.S2, S.S3, S.S4, S.S7, S.S8}) == LLM_STAGES
    assert not {S.S1, S.S5, S.S6, S.S9} & LLM_STAGES


async def _new_run(session_factory: SessionFactory, config: dict[str, object]) -> uuid.UUID:
    async with session_factory() as session:
        doc = await make_document(session)
        run_id = await create_run(session, document_id=doc.id, user_id=doc.user_id, config=config)
        await session.commit()
    return run_id


async def _drain(session_factory: SessionFactory, settings: Settings) -> None:
    registry, _ = counting_registry()
    while await work_once(session_factory, registry, "w1", settings):
        pass


@pytest.mark.db
async def test_worker_fails_llm_stage_when_provider_changed(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = _openai(pipeline_settings())
    run_id = await _new_run(session_factory, {"variant": "V2", "provider": "anthropic"})
    registry, stages = counting_registry()
    while await work_once(session_factory, registry, "w1", settings):
        pass

    async with session_factory() as session:
        run = (
            await session.execute(select(VerificationRun).where(VerificationRun.id == run_id))
        ).scalar_one()
        jobs = {
            S(job.stage): job
            for job in (await session.execute(select(Job).where(Job.run_id == run_id))).scalars()
        }
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None
    assert run.error["stage"] == S.S2.value
    assert run.error["type"] == "ConflictError"
    assert labels.LLM_PROVIDER_CHANGED in run.error["message"]
    # Stage không cần LLM vẫn chạy; stage cần LLM bị chặn ngay, không thử lại.
    assert len(stages[S.S1].calls) == 1
    assert stages[S.S2].calls == []
    assert jobs[S.S1].status == JobStatus.DONE.value
    assert jobs[S.S2].status == JobStatus.FAILED.value
    assert jobs[S.S2].attempts == 1


@pytest.mark.db
async def test_worker_runs_unbound_run_with_any_provider(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = _openai(pipeline_settings())
    run_id = await _new_run(session_factory, {"variant": "V2"})
    registry, stages = counting_registry()
    while await work_once(session_factory, registry, "w1", settings):
        pass
    async with session_factory() as session:
        status = await session.scalar(
            select(VerificationRun.status).where(VerificationRun.id == run_id)
        )
    assert status == RunStatus.COMPLETED.value
    assert all(len(stage.calls) == 1 for stage in stages.values())


@pytest.mark.db
async def test_worker_runs_when_provider_matches(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = _openai(pipeline_settings())
    run_id = await _new_run(session_factory, {"variant": "V2", "provider": OPENAI})
    await _drain(session_factory, settings)
    async with session_factory() as session:
        status = await session.scalar(
            select(VerificationRun.status).where(VerificationRun.id == run_id)
        )
    assert status == RunStatus.COMPLETED.value
