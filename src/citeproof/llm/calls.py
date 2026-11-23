"""Ghi nhật ký gọi LLM (chỉ metadata, không có nội dung prompt/response) và ước tính chi phí."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models.llm import LLMCall


@dataclass(frozen=True)
class LLMCallRecord:
    run_id: UUID | None
    stage: str | None
    task: str
    provider: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_estimate: Decimal
    cache_hit: bool
    cache_key: str
    cache_namespace: str
    agent_run_id: UUID | None = None
    step_index: int | None = None


class CallLogger(Protocol):
    async def log(self, record: LLMCallRecord) -> None: ...


class InMemoryCallLogger:
    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    async def log(self, record: LLMCallRecord) -> None:
        self.records.append(record)


class PgCallLogger:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def log(self, record: LLMCallRecord) -> None:
        row = LLMCall(
            run_id=record.run_id,
            agent_run_id=record.agent_run_id,
            step_index=record.step_index,
            # Cột stage là NOT NULL; bản ghi không thuộc stage nào lưu chuỗi rỗng.
            stage=record.stage or "",
            task=record.task,
            provider=record.provider,
            model=record.model,
            prompt_version=record.prompt_version,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
            cost_estimate=record.cost_estimate,
            cache_hit=record.cache_hit,
            cache_key=record.cache_key,
            cache_namespace=record.cache_namespace,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    prices: Mapping[str, tuple[float, float]],
) -> Decimal:
    """Giá theo 1 triệu token (input, output); model lạ → 0."""
    if model not in prices:
        return Decimal(0)
    price_in, price_out = prices[model]
    total = Decimal(str(price_in)) * input_tokens + Decimal(str(price_out)) * output_tokens
    return total / Decimal(1_000_000)
