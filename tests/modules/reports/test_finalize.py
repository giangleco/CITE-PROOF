"""S9 — hoàn tất lần chạy: `verification_runs.cost_estimate` (spec §7.9; T-601)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, StageName
from citeproof.core.settings import Settings
from citeproof.db.models import LLMCall, VerificationRun
from citeproof.modules.reports.stage import FinalizeStage
from citeproof.pipeline.stages import StageRegistry
from tests.pipeline.helpers import CountingStage, drain, make_test_deps, pipeline_settings
from tests.services.helpers import new_run, new_user

SessionFactory = async_sessionmaker[AsyncSession]


def _call(run_id: uuid.UUID | None, cost: str) -> LLMCall:
    return LLMCall(
        run_id=run_id,
        stage="S8",
        task="verdict.judge",
        provider="scripted",
        model="claude-sonnet-5-5",
        prompt_version="v1",
        input_tokens=10,
        output_tokens=5,
        latency_ms=1,
        cost_estimate=Decimal(cost),
        cache_namespace="doc:x",
    )


@pytest.mark.db
async def test_s9_sets_run_cost(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        run_id = await new_run(s, settings, user)
        other_run = await new_run(s, settings, await new_user(s, "khac@b.vn"))
        s.add_all([_call(run_id, "0.01"), _call(run_id, "0.02"), _call(other_run, "0.5")])
        s.add(_call(None, "1"))
        await s.commit()

    deps = make_test_deps(settings, session_factory)
    registry = StageRegistry(
        FinalizeStage(deps) if name is StageName.S9 else CountingStage(name) for name in StageName
    )
    await drain(session_factory, registry, pipeline_settings())

    async with session_factory() as s:
        run = await s.get(VerificationRun, run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED.value
        assert run.cost_estimate == Decimal("0.03")
        other = await s.scalar(
            select(VerificationRun.cost_estimate).where(VerificationRun.id == other_run)
        )
        assert other == Decimal("0.5")
