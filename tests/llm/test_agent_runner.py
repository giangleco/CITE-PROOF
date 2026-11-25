"""AgentRunner (spec §9, ADR-010): vòng lặp có giới hạn, submit, nhắc, cache từng lượt, quỹ đạo."""

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import AgentName, AgentRunStatus, AgentTargetType
from citeproof.core.errors import RetryableError
from citeproof.db.models.agents import AgentRun
from citeproof.db.models.llm import LLMCall
from citeproof.llm.agent_runner import AgentBudget, AgentOutcome, AgentRunner
from citeproof.llm.cache import InMemoryCacheStore, agent_step_cache_key, doc_namespace
from citeproof.llm.calls import PgCallLogger
from citeproof.llm.context import PromptParts
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.tools import SubmitTool, Tool, ToolError, ToolResult
from citeproof.llm.types import CacheMissError, CacheMode, CallContext, OutputModel, TurnResponse
from citeproof.pipeline.config import TaskSpec
from tests.db.factories import make_document, make_run

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_db")]

MODEL = "claude-sonnet-5-5"
SPEC = TaskSpec(model=MODEL, effort="high", max_tokens=16000, prompt_version="v1")
PARTS = PromptParts(
    task="agent.evidence_investigator", prompt_version="v1", system="SYS", user="USER"
)
PRICES = {MODEL: (2.0, 10.0)}
LAST_CALL = "You have one call left: submit now with what you have."


class Answer(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "answer/1"
    answer: str


class LookupArgs(BaseModel):
    query: str


class LookupTool:
    """Công cụ đọc giả: trả lại truy vấn; đếm số lần chạy."""

    name = "note_lookup"
    description = "Look up a note."
    input_model: type[BaseModel] = LookupArgs

    def __init__(self, *, output: str | None = None, sleep: float = 0.0) -> None:
        self.calls: list[str] = []
        self._output = output
        self._sleep = sleep

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        assert isinstance(args, LookupArgs)
        self.calls.append(args.query)
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if self._output is not None:
            return ToolResult(self._output)
        return ToolResult(json.dumps({"hits": [args.query]}))


def submit_tool(
    errors: Sequence[ToolError] = (), *, when_empty_only: bool = False
) -> SubmitTool[Answer]:
    def check(value: Answer) -> Sequence[ToolError]:
        if when_empty_only and value.answer:
            return []
        return errors

    return SubmitTool(
        name="submit_answer", description="Submit the answer.", model=Answer, check=check
    )


def tool_use(name: str, payload: dict[str, Any], block_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": payload}


def turn(*blocks: dict[str, Any], input_tokens: int = 100, stop: str = "tool_use") -> TurnResponse:
    return TurnResponse(
        content=tuple(blocks),
        stop_reason=stop,
        input_tokens=input_tokens,
        output_tokens=10,
        model=MODEL,
    )


def lookup(query: str, block_id: str, *, input_tokens: int = 100) -> TurnResponse:
    return turn(tool_use("note_lookup", {"query": query}, block_id), input_tokens=input_tokens)


def submit(answer: str, block_id: str) -> TurnResponse:
    return turn(tool_use("submit_answer", {"answer": answer}, block_id))


def end(text: str) -> TurnResponse:
    return turn({"type": "text", "text": text}, stop="end_turn")


def budget(**overrides: Any) -> AgentBudget:
    values: dict[str, Any] = {
        "max_steps": 6,
        "max_context_tokens": 10_000,
        "max_total_input_tokens": 40_000,
        "max_wall_seconds": 60.0,
    }
    values.update(overrides)
    return AgentBudget(**values)


@pytest.fixture
async def ctx(session_factory: async_sessionmaker[AsyncSession]) -> CallContext:
    async with session_factory() as session:
        document = await make_document(session)
        run = await make_run(session, document)
        await session.commit()
        return CallContext(
            namespace=doc_namespace(document.id),
            document_id=document.id,
            run_id=run.id,
            stage="verify",
        )


def runner(
    session_factory: async_sessionmaker[AsyncSession],
    turns: Sequence[TurnResponse | BaseException],
    *,
    mode: CacheMode = CacheMode.READ_WRITE,
    cache: InMemoryCacheStore | None = None,
) -> tuple[AgentRunner, ScriptedProvider]:
    provider = ScriptedProvider(turns=turns)
    agent_runner = AgentRunner(
        provider,
        cache if cache is not None else InMemoryCacheStore(),
        PgCallLogger(session_factory),
        session_factory,
        mode=mode,
        prices=PRICES,
    )
    return agent_runner, provider


async def run(
    agent_runner: AgentRunner,
    ctx: CallContext,
    tools: Sequence[Tool],
    submit_: SubmitTool[Answer] | None = None,
    *,
    agent_budget: AgentBudget | None = None,
    catalog: str = "v1",
) -> AgentOutcome[Answer]:
    return await agent_runner.run(
        agent=AgentName.EVIDENCE_INVESTIGATOR,
        version="v1",
        tool_catalog_version=catalog,
        parts=PARTS,
        tools=tools,
        submit=submit_ or submit_tool(),
        budget=agent_budget or budget(),
        spec=SPEC,
        ctx=ctx,
        target_type=AgentTargetType.CLAIM,
        target_id=uuid.UUID(int=7),
    )


async def agent_row(
    session_factory: async_sessionmaker[AsyncSession], agent_run_id: uuid.UUID
) -> AgentRun:
    async with session_factory() as session:
        row = await session.get(AgentRun, agent_run_id)
        assert row is not None
        return row


async def call_rows(
    session_factory: async_sessionmaker[AsyncSession], agent_run_id: uuid.UUID
) -> list[LLMCall]:
    async with session_factory() as session:
        result = await session.execute(
            select(LLMCall).where(LLMCall.agent_run_id == agent_run_id).order_by(LLMCall.step_index)
        )
        return list(result.scalars())


def tool_results(message: Any) -> list[dict[str, Any]]:
    assert message["role"] == "user"
    return [block for block in message["content"] if block["type"] == "tool_result"]


def texts(messages: Sequence[Any]) -> list[str]:
    found: list[str] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            found.append(content)
            continue
        found.extend(block["text"] for block in content if block.get("type") == "text")
    return found


async def test_agent_submits_and_records_trajectory(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    tool = LookupTool()
    agent_runner, provider = runner(
        session_factory, [lookup("a", "t1"), lookup("b", "t2"), submit("x", "t3")]
    )
    outcome = await run(agent_runner, ctx, [tool])

    assert outcome.status is AgentRunStatus.SUBMITTED
    assert outcome.output == Answer(answer="x")
    assert (outcome.n_steps, outcome.input_tokens, outcome.output_tokens) == (2, 300, 30)
    assert outcome.model == MODEL
    assert tool.calls == ["a", "b"]

    second = provider.turn_requests[1]
    assert second.system == "SYS" and second.model == MODEL and second.effort == "high"
    assert [spec.name for spec in second.tools] == ["note_lookup", "submit_answer"]
    assert second.messages[0] == {"role": "user", "content": "USER"}
    assert second.messages[1] == {"role": "assistant", "content": list(lookup("a", "t1").content)}
    (result,) = tool_results(second.messages[2])
    assert result == {"type": "tool_result", "tool_use_id": "t1", "content": '{"hits": ["a"]}'}

    row = await agent_row(session_factory, outcome.agent_run_id)
    assert row.status == AgentRunStatus.SUBMITTED.value
    assert row.agent == "evidence_investigator" and row.target_type == "CLAIM"
    assert (row.agent_version, row.tool_catalog_version) == ("v1", "v1")
    assert row.run_id == ctx.run_id and row.target_id == uuid.UUID(int=7)
    assert len(row.steps) == 2
    first = row.steps[0]
    assert first["index"] == 0 and first["tool"] == "note_lookup"
    assert first["args"] == {"query": "a"}
    assert first["result_preview"] == '{"hits": ["a"]}'
    assert first["error_code"] is None and first["cache_hit"] is False
    assert row.final_output == {"answer": "x"}
    assert (row.n_steps, row.input_tokens, row.output_tokens) == (2, 300, 30)
    assert row.cost_estimate > 0
    assert row.started_at is not None and row.finished_at is not None

    calls = await call_rows(session_factory, outcome.agent_run_id)
    assert [c.step_index for c in calls] == [0, 1, 2]
    assert all(c.run_id == ctx.run_id and c.task == PARTS.task for c in calls)
    assert all(c.cost_estimate > 0 and not c.cache_hit for c in calls)
    assert sum(c.cost_estimate for c in calls) == row.cost_estimate


async def test_parallel_tool_calls_one_result_message(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    started = 0
    both = asyncio.Event()

    class Concurrent(LookupTool):
        async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
            nonlocal started
            started += 1
            if started == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)  # nối tiếp thì lời gọi đầu hết giờ
            return await super().__call__(args)

    pair = turn(
        tool_use("note_lookup", {"query": "a"}, "p1"), tool_use("note_lookup", {"query": "b"}, "p2")
    )
    agent_runner, provider = runner(session_factory, [pair, submit("x", "s")])
    outcome = await run(agent_runner, ctx, [Concurrent()])

    assert outcome.status is AgentRunStatus.SUBMITTED
    assert outcome.n_steps == 2
    last = provider.turn_requests[1].messages[-1]
    assert [r["tool_use_id"] for r in tool_results(last)] == ["p1", "p2"]
    assert len(provider.turn_requests[1].messages) == 3


async def test_unknown_tool_returns_error_result(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    agent_runner, provider = runner(
        session_factory, [turn(tool_use("web_search", {"q": "x"}, "u1")), submit("x", "s")]
    )
    outcome = await run(agent_runner, ctx, [LookupTool()])

    assert outcome.status is AgentRunStatus.SUBMITTED
    (result,) = tool_results(provider.turn_requests[1].messages[-1])
    assert result["is_error"] is True
    error = json.loads(result["content"])["error"]
    assert error["code"] == "UNKNOWN_TOOL"
    assert "note_lookup" in error["fix"]
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert row.steps[0]["error_code"] == "UNKNOWN_TOOL"


async def test_invalid_tool_arguments_return_error_result(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    tool = LookupTool()
    agent_runner, provider = runner(
        session_factory, [turn(tool_use("note_lookup", {"limit": 3}, "i1")), submit("x", "s")]
    )
    await run(agent_runner, ctx, [tool])

    (result,) = tool_results(provider.turn_requests[1].messages[-1])
    assert result["is_error"] is True
    assert json.loads(result["content"])["error"]["code"] == "INVALID_ARGUMENTS"
    assert tool.calls == []


async def test_steps_budget_exhausted(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    tool = LookupTool()
    agent_runner, provider = runner(
        session_factory, [lookup("a", "t1"), lookup("b", "t2"), lookup("c", "t3")]
    )
    outcome = await run(agent_runner, ctx, [tool], agent_budget=budget(max_steps=2))

    assert outcome.status is AgentRunStatus.BUDGET_EXHAUSTED
    assert outcome.output is None
    assert tool.calls == ["a", "b"]  # lượt cuối không được chạy công cụ
    assert len(provider.turn_requests) == 3
    final = provider.turn_requests[-1]
    assert texts(final.messages[-1:]) == [LAST_CALL]
    assert texts(final.messages).count(LAST_CALL) == 1
    assert LAST_CALL not in texts(provider.turn_requests[1].messages)
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert row.status == AgentRunStatus.BUDGET_EXHAUSTED.value and len(row.steps) == 2


async def test_parallel_calls_beyond_step_budget_are_refused(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    tool = LookupTool()
    trio = turn(*(tool_use("note_lookup", {"query": q}, f"p{q}") for q in "abc"))
    agent_runner, provider = runner(session_factory, [trio, submit("x", "s")])
    outcome = await run(agent_runner, ctx, [tool], agent_budget=budget(max_steps=2))

    assert outcome.status is AgentRunStatus.SUBMITTED
    assert tool.calls == ["a", "b"]
    results = tool_results(provider.turn_requests[1].messages[-1])
    assert [r.get("is_error", False) for r in results] == [False, False, True]
    assert json.loads(results[2]["content"])["error"]["code"] == "STEP_BUDGET_EXHAUSTED"
    assert texts(provider.turn_requests[1].messages[-1:]) == [LAST_CALL]


async def test_total_input_tokens_budget(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    tool = LookupTool()
    turns = [lookup(q, f"t{q}") for q in "abcde"]
    agent_runner, provider = runner(session_factory, turns)
    outcome = await run(agent_runner, ctx, [tool], agent_budget=budget(max_total_input_tokens=250))

    assert outcome.status is AgentRunStatus.BUDGET_EXHAUSTED
    assert len(provider.turn_requests) == 3  # 100, 200, 300 > 250
    assert tool.calls == ["a", "b"]
    assert outcome.input_tokens == 300


async def test_context_tokens_budget(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    tool = LookupTool()
    turns = [
        lookup("a", "t1", input_tokens=100),
        lookup("b", "t2", input_tokens=200),
        submit("x", "s"),
    ]
    agent_runner, provider = runner(session_factory, turns)
    outcome = await run(agent_runner, ctx, [tool], agent_budget=budget(max_context_tokens=150))

    assert outcome.status is AgentRunStatus.BUDGET_EXHAUSTED
    assert len(provider.turn_requests) == 2
    assert tool.calls == ["a"]


async def test_submit_accepted_even_when_turn_exceeds_token_budget(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    big_submit = turn(tool_use("submit_answer", {"answer": "x"}, "s"), input_tokens=500)
    agent_runner, _ = runner(session_factory, [big_submit])
    outcome = await run(
        agent_runner, ctx, [LookupTool()], agent_budget=budget(max_context_tokens=150)
    )
    assert outcome.status is AgentRunStatus.SUBMITTED


async def test_no_submit_nudges_once_then_no_submit(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    agent_runner, provider = runner(session_factory, [end("I think yes."), end("Done.")])
    outcome = await run(agent_runner, ctx, [LookupTool()])

    assert outcome.status is AgentRunStatus.NO_SUBMIT
    assert outcome.output is None
    assert len(provider.turn_requests) == 2
    nudges = [t for t in texts(provider.turn_requests[1].messages) if "submit_answer" in t]
    assert len(nudges) == 1
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert row.status == AgentRunStatus.NO_SUBMIT.value


async def test_nudge_after_empty_turn_keeps_conversation_valid(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    agent_runner, provider = runner(session_factory, [turn(stop="end_turn"), submit("x", "s")])
    outcome = await run(agent_runner, ctx, [LookupTool()])
    assert outcome.status is AgentRunStatus.SUBMITTED
    roles = [m["role"] for m in provider.turn_requests[1].messages]
    assert "assistant" not in roles  # API từ chối lượt assistant rỗng


async def test_submit_validation_error_then_fixed(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    error = ToolError("EMPTY_ANSWER", "The answer is empty.", "Provide a non-empty answer.")
    agent_runner, provider = runner(session_factory, [submit("", "s1"), submit("ok", "s2")])
    outcome = await run(
        agent_runner, ctx, [LookupTool()], submit_tool([error], when_empty_only=True)
    )

    assert outcome.status is AgentRunStatus.SUBMITTED
    assert outcome.output == Answer(answer="ok")
    (result,) = tool_results(provider.turn_requests[1].messages[-1])
    assert result["is_error"] is True and result["tool_use_id"] == "s1"
    assert json.loads(result["content"])["error"] == {
        "code": "EMPTY_ANSWER",
        "message": "The answer is empty.",
        "fix": "Provide a non-empty answer.",
    }
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert outcome.n_steps == 0
    assert [s["error_code"] for s in row.steps] == ["EMPTY_ANSWER"]


async def test_submit_schema_error_is_returned_to_the_model(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    bad = turn(tool_use("submit_answer", {"verdict": 1}, "s1"))
    agent_runner, provider = runner(session_factory, [bad, submit("ok", "s2")])
    outcome = await run(agent_runner, ctx, [LookupTool()])
    assert outcome.output == Answer(answer="ok")
    (result,) = tool_results(provider.turn_requests[1].messages[-1])
    assert json.loads(result["content"])["error"]["code"] == "INVALID_ARGUMENTS"


async def test_submit_errors_exhausted_returns_last(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    error = ToolError("BAD", "Always wrong.", "Try again.")
    agent_runner, provider = runner(session_factory, [submit("a", "s1"), submit("b", "s2")])
    outcome = await run(agent_runner, ctx, [LookupTool()], submit_tool([error]))

    assert outcome.status is AgentRunStatus.SUBMITTED
    assert outcome.output == Answer(answer="b")
    assert len(provider.turn_requests) == 2
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert row.final_output == {"answer": "b"}


async def test_replay_reproduces_trajectory(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    cache = InMemoryCacheStore()
    first_runner, _ = runner(session_factory, [lookup("a", "t1"), submit("x", "s")], cache=cache)
    first = await run(first_runner, ctx, [LookupTool()])

    replay_runner, provider = runner(session_factory, [], mode=CacheMode.REPLAY_ONLY, cache=cache)
    replay = await run(replay_runner, ctx, [LookupTool()])

    assert provider.turn_requests == []
    assert (replay.status, replay.output, replay.n_steps) == (
        first.status,
        first.output,
        first.n_steps,
    )
    assert replay.input_tokens == first.input_tokens
    assert replay.agent_run_id != first.agent_run_id
    first_row = await agent_row(session_factory, first.agent_run_id)
    replay_row = await agent_row(session_factory, replay.agent_run_id)

    def strip(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in s.items() if k != "cache_hit"} for s in steps]

    assert strip(replay_row.steps) == strip(first_row.steps)
    assert all(s["cache_hit"] for s in replay_row.steps)
    assert replay_row.cost_estimate == 0
    calls = await call_rows(session_factory, replay.agent_run_id)
    assert [(c.step_index, c.cache_hit, c.input_tokens) for c in calls] == [
        (0, True, 0),
        (1, True, 0),
    ]


async def test_replay_cache_miss_raises_and_marks_error(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    agent_runner, provider = runner(session_factory, [], mode=CacheMode.REPLAY_ONLY)
    with pytest.raises(CacheMissError):
        await run(agent_runner, ctx, [LookupTool()])
    assert provider.turn_requests == []
    async with session_factory() as session:
        statuses = (await session.execute(select(AgentRun.status))).scalars().all()
    assert statuses == [AgentRunStatus.ERROR.value]


async def test_cache_key_changes_with_catalog_version(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    cache = InMemoryCacheStore()
    first_runner, _ = runner(session_factory, [submit("x", "s")], cache=cache)
    first = await run(first_runner, ctx, [LookupTool()])

    tools_payload = [
        {
            "name": "note_lookup",
            "description": "Look up a note.",
            "input_schema": LookupArgs.model_json_schema(),
        },
        {
            "name": "submit_answer",
            "description": "Submit the answer.",
            "input_schema": Answer.model_json_schema(),
        },
    ]
    expected = agent_step_cache_key(
        namespace=ctx.namespace,
        agent="evidence_investigator",
        agent_version="v1",
        tool_catalog_version="v1",
        model=MODEL,
        messages=[
            {"role": "system", "content": "SYS"},
            {"role": "tools", "content": tools_payload},
            {"role": "user", "content": "USER"},
        ],
    )
    (call,) = await call_rows(session_factory, first.agent_run_id)
    assert call.cache_key == expected

    same_runner, _ = runner(session_factory, [], mode=CacheMode.REPLAY_ONLY, cache=cache)
    assert (await run(same_runner, ctx, [LookupTool()])).status is AgentRunStatus.SUBMITTED
    other_runner, _ = runner(session_factory, [], mode=CacheMode.REPLAY_ONLY, cache=cache)
    with pytest.raises(CacheMissError):
        await run(other_runner, ctx, [LookupTool()], catalog="v2")


async def test_provider_error_status_error(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    seen: list[str] = []

    class Probe(LookupTool):
        async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
            async with session_factory() as session:
                seen.extend((await session.execute(select(AgentRun.status))).scalars())
            return await super().__call__(args)

    agent_runner, _ = runner(
        session_factory, [lookup("a", "t1"), RetryableError("anthropic X status=529")]
    )
    outcome = await run(agent_runner, ctx, [Probe()])

    assert seen == [AgentRunStatus.ERROR.value]  # dòng được ghi ngay khi bắt đầu
    assert outcome.status is AgentRunStatus.ERROR and outcome.output is None
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert row.status == AgentRunStatus.ERROR.value
    assert row.finished_at is not None and len(row.steps) == 1
    calls = await call_rows(session_factory, outcome.agent_run_id)
    assert [c.step_index for c in calls] == [0]


async def test_wall_clock_budget(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    agent_runner, provider = runner(session_factory, [lookup("a", "t1"), submit("x", "s")])
    started = time.monotonic()
    outcome = await run(
        agent_runner, ctx, [LookupTool(sleep=5.0)], agent_budget=budget(max_wall_seconds=0.2)
    )
    assert time.monotonic() - started < 2.0
    assert outcome.status is AgentRunStatus.BUDGET_EXHAUSTED
    assert len(provider.turn_requests) == 1


async def test_tool_output_truncated(
    session_factory: async_sessionmaker[AsyncSession], ctx: CallContext
) -> None:
    agent_runner, provider = runner(session_factory, [lookup("a", "t1"), submit("x", "s")])
    outcome = await run(agent_runner, ctx, [LookupTool(output="x" * 7000)])

    (result,) = tool_results(provider.turn_requests[1].messages[-1])
    assert result["content"].startswith("x" * 6000)
    assert result["content"].endswith("[truncated]")
    assert len(result["content"]) < 6100
    row = await agent_row(session_factory, outcome.agent_run_id)
    assert len(row.steps[0]["result_preview"]) == 2000
