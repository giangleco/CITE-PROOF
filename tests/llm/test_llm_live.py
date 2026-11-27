"""Gọi LLM thật qua provider đang cấu hình (`make_provider`).

Chỉ chạy khi `CITEPROOF_LIVE_LLM=1` và `Settings().llm_configured`; server local tương thích
OpenAI không cần khóa. Dùng model của tác vụ `reference.parse` cho cả worker lẫn agent.
"""

import os
import uuid
from collections.abc import AsyncIterator
from typing import ClassVar

import pytest
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import AgentName, AgentRunStatus, AgentTargetType
from citeproof.core.settings import Settings
from citeproof.llm.agent_runner import AgentBudget
from citeproof.llm.cache import doc_namespace
from citeproof.llm.context import PromptParts
from citeproof.llm.factory import make_agent_runner, make_llm_client, make_provider
from citeproof.llm.tools import SubmitTool
from citeproof.llm.types import CacheMode, CallContext, OutputModel
from citeproof.pipeline.config import TaskSpec
from tests.db.factories import make_document, make_run

TASK = "reference.parse"


def _configured() -> bool:
    try:
        return Settings().llm_configured
    except ValueError:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.db,
    pytest.mark.usefixtures("clean_db"),
    pytest.mark.skipif(
        os.environ.get("CITEPROOF_LIVE_LLM") != "1" or not _configured(),
        reason="cần CITEPROOF_LIVE_LLM=1 và một provider LLM đã cấu hình",
    ),
]


class Capital(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "live_capital/1"
    city: str = Field(max_length=100)


@pytest.fixture
async def ctx(session_factory: async_sessionmaker[AsyncSession]) -> CallContext:
    async with session_factory() as session:
        document = await make_document(session)
        run = await make_run(session, document)
        await session.commit()
        return CallContext(doc_namespace(document.id), document.id, run.id, "live")


@pytest.fixture
async def settings() -> AsyncIterator[Settings]:
    yield Settings()


async def test_live_structured_output(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    provider = make_provider(settings)
    assert provider is not None
    client = make_llm_client(settings, session_factory, mode=CacheMode.FRESH, provider=provider)
    assert client is not None
    parts = PromptParts(
        task="live.capital",
        prompt_version="v1",
        system="Answer the question. Reply with the JSON object only.",
        user='What is the capital city of France? Reply as {"city": "..."}.',
    )
    try:
        result = await client.complete_structured(
            parts=parts,
            schema=Capital,
            model=settings.llm_models[TASK],
            ctx=ctx,
            max_tokens=1000,
            effort=settings.llm_effort[TASK],
        )
    finally:
        await client.aclose()
    assert "paris" in result.value.city.lower()
    assert result.model


async def test_live_agent_turn_with_one_tool(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    provider = make_provider(settings)
    assert provider is not None
    runner = make_agent_runner(settings, session_factory, mode=CacheMode.FRESH, provider=provider)
    assert runner is not None
    submit = SubmitTool(
        name="submit_answer",
        description="Submit the final answer. Call this tool exactly once.",
        model=Capital,
        check=lambda _value: [],
    )
    parts = PromptParts(
        task="agent.live",
        prompt_version="v1",
        system="You must answer by calling the submit_answer tool.",
        user="What is the capital city of France? Submit it with submit_answer.",
    )
    try:
        outcome = await runner.run(
            agent=AgentName.EVIDENCE_INVESTIGATOR,
            version="v1",
            tool_catalog_version="v1",
            parts=parts,
            tools=(),
            submit=submit,
            budget=AgentBudget(
                max_steps=4,
                max_context_tokens=20_000,
                max_total_input_tokens=60_000,
                max_wall_seconds=300.0,
            ),
            spec=TaskSpec(
                model=settings.llm_models[TASK],
                effort=settings.llm_effort[TASK],
                max_tokens=settings.llm_max_tokens[TASK],
                prompt_version="v1",
            ),
            ctx=ctx,
            target_type=AgentTargetType.CLAIM,
            target_id=uuid.uuid4(),
        )
    finally:
        await provider.aclose()  # type: ignore[attr-defined]
    assert outcome.status is AgentRunStatus.SUBMITTED
    assert outcome.output is not None and "paris" in outcome.output.city.lower()
