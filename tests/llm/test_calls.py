from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models.llm import LLMCall
from citeproof.llm.calls import InMemoryCallLogger, LLMCallRecord, PgCallLogger, estimate_cost


def _record(stage: str | None = "extract") -> LLMCallRecord:
    return LLMCallRecord(
        run_id=None,
        stage=stage,
        task="t",
        provider="p",
        model="m",
        prompt_version="v1",
        input_tokens=10,
        output_tokens=5,
        latency_ms=42,
        cost_estimate=Decimal("0.001234"),
        cache_hit=True,
        cache_key="k",
        cache_namespace="eval:D@h",
    )


def test_estimate_cost() -> None:
    assert estimate_cost("m", 1_000_000, 500_000, {"m": (2.0, 10.0)}) == Decimal("7")
    assert estimate_cost("unknown", 10, 10, {}) == Decimal(0)


async def test_in_memory_logger_collects() -> None:
    logger = InMemoryCallLogger()
    rec = _record()
    await logger.log(rec)
    assert logger.records == [rec]


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_pg_call_logger_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await PgCallLogger(session_factory).log(_record())
    async with session_factory() as session:
        row = (await session.execute(select(LLMCall))).scalar_one()
    assert (row.stage, row.task, row.provider, row.model) == ("extract", "t", "p", "m")
    assert (row.input_tokens, row.output_tokens, row.latency_ms) == (10, 5, 42)
    assert row.cost_estimate == Decimal("0.001234")
    assert row.cache_hit is True
    assert (row.cache_key, row.cache_namespace) == ("k", "eval:D@h")
    assert row.stage == "extract"
    assert row.run_id is None and row.agent_run_id is None and row.step_index is None


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_pg_call_logger_stores_null_stage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await PgCallLogger(session_factory).log(_record(stage=None))
    async with session_factory() as session:
        assert (await session.execute(select(LLMCall.stage))).scalar_one() is None
