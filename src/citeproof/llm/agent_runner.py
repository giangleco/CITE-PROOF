"""AgentRunner: vòng lặp agent có giới hạn (spec §9, `07a` §4.5.1, ADR-010).

Mỗi lượt: gọi `complete_turn` → chạy song song các lời gọi công cụ → trả mọi `tool_result`
trong một lượt người dùng. Dừng khi:

- `submit_*` hợp lệ → `SUBMITTED`. Submit sai (schema hoặc `check`) → `is_error` kèm `fix`;
  sau `max_submit_attempts` lần sai, nhận bản cuối đã parse được (validator tất định hạ mức).
- hết ngân sách → `BUDGET_EXHAUSTED`: `max_steps` đếm lời gọi công cụ, không tính submit (kể cả
  công cụ lạ hay đối số sai); khi đã dùng hết, lượt kết quả thêm `LAST_CALL_TEXT` và lượt kế là
  lượt cuối, chỉ còn submit. `max_context_tokens` so với input của một lượt,
  `max_total_input_tokens` với tổng input; `max_wall_seconds` cắt cả lời gọi LLM lẫn công cụ.
  Submit hợp lệ trong lượt vượt ngân sách token vẫn được nhận (token đã tiêu).
- model dừng mà chưa nộp → nhắc một lần → `NO_SUBMIT`.
- lỗi provider không tạm thời (`CiteProofError`) → `ERROR`. Lỗi cache (`CacheMissError`,
  `CacheEntryInvalidError`), `InternalError`, `RetryableError`, `LeaseLostError` và lỗi lập trình
  được ném lại sau khi ghi `ERROR`; với `RetryableError`, stage gọi ném lại để job được thử lại,
  trừ lần thử cuối thì coi như `ERROR`.

Với trạng thái khác `SUBMITTED`, `output` là bản nộp cuối đã parse được nhưng bị từ chối (nếu
có); A1 bỏ qua nó, A2 có thể dùng.

Hội thoại chỉ được nối thêm, không sửa hay bỏ khối cũ (khối thinking gửi lại nguyên vẹn). Mỗi
lượt được cache bằng `agent_step_cache_key` trên hội thoại gồm system prompt và danh sách công
cụ; entry giữ cả số token để phát lại quyết định ngân sách giống hệt lần đầu. `agent_runs` được
ghi ngay khi bắt đầu với trạng thái `ERROR` (lần chạy bị ngắt giữa chừng vẫn để lại dấu vết) và
cập nhật khi kết thúc; mỗi lượt ghi một dòng `llm_calls` (commit ngay, cổng chi phí S8 thấy được)
với `agent_run_id`, `step_index`. Không log prompt, kết quả công cụ hay đầu ra LLM; bản rút gọn
chỉ nằm trong `agent_runs.steps`.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Self
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import AgentName, AgentRunStatus, AgentTargetType
from citeproof.core.errors import CiteProofError, InternalError, LeaseLostError, RetryableError
from citeproof.db.models.agents import AgentRun
from citeproof.llm.cache import CacheEntryMeta, CacheStore, agent_step_cache_key, validate_namespace
from citeproof.llm.calls import CallLogger, LLMCallRecord, estimate_cost
from citeproof.llm.context import PromptParts
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.tools import SubmitTool, Tool, ToolError
from citeproof.llm.types import (
    CacheEntryInvalidError,
    CacheMissError,
    CacheMode,
    CallContext,
    OutputModel,
    ToolSpec,
    TurnRequest,
    TurnResponse,
)
from citeproof.pipeline.config import AgentConfig, TaskSpec

logger = logging.getLogger(__name__)

LAST_CALL_TEXT = "You have one call left: submit now with what you have."
NUDGE_TEXT = "You stopped without calling {submit}. Call {submit} now with what you have."
# Giới hạn kết quả một lời gọi công cụ gửi cho model (mô tả công cụ ghi giới hạn này).
TOOL_OUTPUT_LIMIT = 6_000
TRUNCATED_MARK = "\n[truncated]"
# Bản rút gọn kết quả lưu trong `agent_runs.steps` (spec §13).
PREVIEW_LIMIT = 2_000

# Ném lại sau khi ghi ERROR: lỗi cache và lỗi lập trình; lỗi tạm thời (stage gọi thử lại
# job, hoặc coi là ERROR ở lần thử cuối theo `is_last_attempt`); lease đã mất.
_PROPAGATE = (
    CacheMissError,
    CacheEntryInvalidError,
    InternalError,
    RetryableError,
    LeaseLostError,
)


@dataclass(frozen=True)
class AgentBudget:
    max_steps: int
    max_context_tokens: int
    max_total_input_tokens: int
    max_wall_seconds: float
    max_submit_attempts: int = 2

    @classmethod
    def from_config(cls, cfg: AgentConfig) -> Self:
        return cls(
            max_steps=cfg.max_steps,
            max_context_tokens=cfg.max_context_tokens,
            max_total_input_tokens=cfg.max_total_input_tokens,
            max_wall_seconds=cfg.max_wall_seconds,
        )


@dataclass(frozen=True)
class AgentOutcome[T]:
    status: AgentRunStatus
    output: T | None
    agent_run_id: UUID
    n_steps: int
    # Token theo quỹ đạo (cả lượt lấy từ cache), là số ngân sách đã đếm.
    input_tokens: int
    output_tokens: int
    # Model trả lời lượt cuối (model đã yêu cầu nếu chưa có lượt nào).
    model: str


class _OutOfTime(Exception):
    """Hết `max_wall_seconds` khi đang chờ LLM hoặc công cụ."""


@dataclass
class _State[T]:
    model: str
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]] = field(default_factory=list)
    n_steps: int = 0
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    submit_failures: int = 0
    nudged: bool = False
    last_submission: T | None = None


@dataclass(frozen=True)
class _Run[T: OutputModel]:
    """Tham số cố định của một lần chạy agent."""

    agent: AgentName
    version: str
    tool_catalog_version: str
    parts: PromptParts
    tools: Mapping[str, Tool]
    specs: tuple[ToolSpec, ...]
    submit: SubmitTool[T]
    budget: AgentBudget
    spec: TaskSpec
    ctx: CallContext
    agent_run_id: UUID
    deadline: float


@dataclass(frozen=True)
class _Call:
    """Kết quả một lời gọi công cụ, trước khi gửi lại model."""

    use: Mapping[str, Any]
    content: str
    error_code: str | None


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        cache: CacheStore,
        call_logger: CallLogger,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        mode: CacheMode = CacheMode.READ_WRITE,
        prices: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._call_logger = call_logger
        self._session_factory = session_factory
        self._mode = mode
        self._prices: Mapping[str, tuple[float, float]] = prices or {}

    async def run[T: OutputModel](
        self,
        *,
        agent: AgentName,
        version: str,
        tool_catalog_version: str,
        parts: PromptParts,
        tools: Sequence[Tool],
        submit: SubmitTool[T],
        budget: AgentBudget,
        spec: TaskSpec,
        ctx: CallContext,
        target_type: AgentTargetType,
        target_id: UUID,
    ) -> AgentOutcome[T]:
        validate_namespace(ctx.namespace, ctx.document_id)
        if ctx.run_id is None:
            raise ValueError("AgentRunner cần ctx.run_id (agent_runs.run_id)")
        by_name: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in by_name or tool.name == submit.name:
                raise ValueError(f"tên công cụ trùng: {tool.name!r}")
            by_name[tool.name] = tool
        specs = (
            *(ToolSpec(t.name, t.description, t.input_model.model_json_schema()) for t in tools),
            ToolSpec(submit.name, submit.description, submit.model.model_json_schema()),
        )

        agent_run_id = await self._start(
            ctx.run_id, agent, version, tool_catalog_version, target_type, target_id
        )
        cfg = _Run(
            agent=agent,
            version=version,
            tool_catalog_version=tool_catalog_version,
            parts=parts,
            tools=by_name,
            specs=specs,
            submit=submit,
            budget=budget,
            spec=spec,
            ctx=ctx,
            agent_run_id=agent_run_id,
            deadline=asyncio.get_running_loop().time() + budget.max_wall_seconds,
        )
        state: _State[T] = _State(
            model=spec.model, messages=[{"role": "user", "content": parts.user}]
        )
        try:
            status, output = await self._loop(cfg, state)
        except _OutOfTime:
            status, output = AgentRunStatus.BUDGET_EXHAUSTED, state.last_submission
        except _PROPAGATE:
            await self._finish(agent_run_id, state, AgentRunStatus.ERROR, None)
            raise
        except CiteProofError as exc:
            # Chỉ tên lớp và mã lỗi: thông điệp lỗi provider không được vào log.
            logger.warning(
                "agent_run_error",
                extra={
                    "agent": agent.value,
                    "agent_run_id": str(agent_run_id),
                    "error": type(exc).__name__,
                    "code": exc.code,
                },
            )
            status, output = AgentRunStatus.ERROR, None
        except Exception:
            await self._finish(agent_run_id, state, AgentRunStatus.ERROR, None)
            raise
        await self._finish(agent_run_id, state, status, output)
        logger.info(
            "agent_run_finished",
            extra={
                "agent": agent.value,
                "agent_run_id": str(agent_run_id),
                "status": status.value,
                "n_steps": state.n_steps,
                "turns": state.turns,
            },
        )
        return AgentOutcome(
            status=status,
            output=output,
            agent_run_id=agent_run_id,
            n_steps=state.n_steps,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            model=state.model,
        )

    async def _loop[T: OutputModel](
        self, cfg: _Run[T], state: _State[T]
    ) -> tuple[AgentRunStatus, T | None]:
        budget = cfg.budget
        submit = cfg.submit
        while True:
            final = state.n_steps >= budget.max_steps
            turn_index = state.turns
            response, cache_hit = await self._turn(cfg, state, turn_index)
            state.turns += 1
            if response.content:  # API từ chối lượt assistant rỗng
                state.messages.append(
                    {"role": "assistant", "content": [dict(b) for b in response.content]}
                )
            over_budget = (
                response.input_tokens > budget.max_context_tokens
                or state.input_tokens > budget.max_total_input_tokens
            )
            uses = [b for b in response.content if b.get("type") == "tool_use"]
            if not uses:
                if final or over_budget:
                    return AgentRunStatus.BUDGET_EXHAUSTED, state.last_submission
                if state.nudged:
                    return AgentRunStatus.NO_SUBMIT, state.last_submission
                state.nudged = True
                nudge = NUDGE_TEXT.format(submit=submit.name)
                state.messages.append({"role": "user", "content": [_text(nudge)]})
                continue

            submit_calls: dict[int, _Call] = {}
            for use in uses:
                if use.get("name") != submit.name:
                    continue
                value, errors = _check_submit(submit, use.get("input"))
                if not errors:
                    return AgentRunStatus.SUBMITTED, value
                state.submit_failures += 1
                if value is not None:
                    state.last_submission = value
                submit_calls[id(use)] = _Call(use, _error_content(errors), errors[0].code)
            if submit_calls and state.submit_failures >= budget.max_submit_attempts:
                if state.last_submission is not None:
                    return AgentRunStatus.SUBMITTED, state.last_submission
                return AgentRunStatus.NO_SUBMIT, None
            if over_budget or (final and not submit_calls):
                return AgentRunStatus.BUDGET_EXHAUSTED, state.last_submission

            others = [u for u in uses if u.get("name") != submit.name]
            allowance = max(budget.max_steps - state.n_steps, 0)
            to_run, refused = others[:allowance], others[allowance:]
            executed = await _within(
                cfg.deadline, asyncio.gather(*(self._execute(cfg, use) for use in to_run))
            )
            state.n_steps += len(to_run)
            calls: dict[int, _Call] = {**submit_calls, **{id(c.use): c for c in executed}}
            refusal = ToolError(
                "STEP_BUDGET_EXHAUSTED",
                "The tool-call budget is used up; this call was not run.",
                f"Call {submit.name} now with what you have.",
            )
            for use in refused:
                calls[id(use)] = _Call(use, _error_content([refusal]), refusal.code)
            results: list[dict[str, Any]] = []
            first_of_turn = True
            for use in uses:
                call = calls[id(use)]
                results.append(_tool_result(call))
                state.steps.append(
                    {
                        "index": len(state.steps),
                        "turn": turn_index,
                        "tool": str(use.get("name")),
                        "args": use.get("input"),
                        "result_preview": call.content[:PREVIEW_LIMIT],
                        "error_code": call.error_code,
                        # Token của lượt gắn vào bước đầu tiên của lượt đó.
                        "input_tokens": response.input_tokens if first_of_turn else 0,
                        "output_tokens": response.output_tokens if first_of_turn else 0,
                        "cache_hit": cache_hit,
                    }
                )
                first_of_turn = False
            if state.n_steps >= budget.max_steps:
                results.append(_text(LAST_CALL_TEXT))
            state.messages.append({"role": "user", "content": results})

    async def _turn[T: OutputModel](
        self, cfg: _Run[T], state: _State[T], turn_index: int
    ) -> tuple[TurnResponse, bool]:
        """Một lượt LLM qua cache; cộng token vào ngân sách và ghi `llm_calls`."""
        model = cfg.spec.model
        key = agent_step_cache_key(
            namespace=cfg.ctx.namespace,
            agent=cfg.agent.value,
            agent_version=cfg.version,
            tool_catalog_version=cfg.tool_catalog_version,
            model=model,
            messages=[
                {"role": "system", "content": cfg.parts.system},
                {"role": "tools", "content": [asdict(s) for s in cfg.specs]},
                *state.messages,
            ],
        )
        response: TurnResponse | None = None
        if self._mode is not CacheMode.FRESH:
            cached = await self._cache.get(key)
            if cached is not None:
                response = _entry_turn(cached)
                if response is None:
                    if self._mode is CacheMode.REPLAY_ONLY:
                        raise CacheEntryInvalidError(key)
                    logger.warning(
                        "llm_cache_entry_invalid",
                        extra={"task": cfg.parts.task, "cache_key": key},
                    )
                    await self._cache.delete(key)
            elif self._mode is CacheMode.REPLAY_ONLY:
                raise CacheMissError(key)
        cache_hit = response is not None
        latency_ms = 0
        if response is None:
            if asyncio.get_running_loop().time() >= cfg.deadline:
                raise _OutOfTime
            request = TurnRequest(
                model=model,
                system=cfg.parts.system,
                messages=tuple(state.messages),
                tools=cfg.specs,
                max_tokens=cfg.spec.max_tokens,
                effort=cfg.spec.effort,
            )
            started = time.perf_counter()
            fresh = await _within(cfg.deadline, self._provider.complete_turn(request))
            latency_ms = round((time.perf_counter() - started) * 1000)
            response = fresh
            if self._mode is CacheMode.READ_WRITE:
                stored = await self._cache.put(
                    key,
                    CacheEntryMeta(
                        cfg.ctx.namespace,
                        cfg.ctx.document_id,
                        cfg.parts.task,
                        model,
                        cfg.version,
                        cfg.submit.model.SCHEMA_VERSION,
                    ),
                    _entry(fresh),
                )
                # Lời gọi đồng thời cùng khóa: entry có trước thắng, để quỹ đạo bây giờ trùng
                # với quỹ đạo phát lại sau này.
                response = _entry_turn(stored) or fresh

        served = response.model or model
        state.model = served
        state.input_tokens += response.input_tokens
        state.output_tokens += response.output_tokens
        # Cache hit không tốn tiền: `llm_calls` ghi 0 token như LLMClient.
        logged_in = 0 if cache_hit else response.input_tokens
        logged_out = 0 if cache_hit else response.output_tokens
        price_model = served if served in self._prices else model
        cost = estimate_cost(price_model, logged_in, logged_out, self._prices)
        state.cost += cost
        await self._call_logger.log(
            LLMCallRecord(
                run_id=cfg.ctx.run_id,
                stage=cfg.ctx.stage,
                task=cfg.parts.task,
                provider=self._provider.name,
                model=served,
                prompt_version=cfg.parts.prompt_version,
                input_tokens=logged_in,
                output_tokens=logged_out,
                latency_ms=latency_ms,
                cost_estimate=cost,
                cache_hit=cache_hit,
                cache_key=key,
                cache_namespace=cfg.ctx.namespace,
                agent_run_id=cfg.agent_run_id,
                step_index=turn_index,
            )
        )
        return response, cache_hit

    async def _execute[T: OutputModel](self, cfg: _Run[T], use: Mapping[str, Any]) -> _Call:
        name = str(use.get("name"))
        tool = cfg.tools.get(name)
        if tool is None:
            names = ", ".join([*cfg.tools, cfg.submit.name])
            error = ToolError(
                "UNKNOWN_TOOL", f"There is no tool named {name!r}.", f"Call one of: {names}."
            )
            return _Call(use, _error_content([error]), error.code)
        try:
            args = tool.input_model.model_validate(use.get("input"))
        except ValidationError as exc:
            error = _invalid_arguments(exc, name)
            return _Call(use, _error_content([error]), error.code)
        result = await tool(args)
        if isinstance(result, ToolError):
            return _Call(use, _error_content([result]), result.code)
        return _Call(use, _truncate(result.content), None)

    async def _start(
        self,
        run_id: UUID,
        agent: AgentName,
        version: str,
        tool_catalog_version: str,
        target_type: AgentTargetType,
        target_id: UUID,
    ) -> UUID:
        stmt = (
            insert(AgentRun)
            .values(
                run_id=run_id,
                agent=agent.value,
                agent_version=version,
                tool_catalog_version=tool_catalog_version,
                target_type=target_type.value,
                target_id=target_id,
                # Bị ngắt giữa chừng (tiến trình chết, hủy) thì dòng vẫn là ERROR.
                status=AgentRunStatus.ERROR.value,
                started_at=datetime.now(UTC),
            )
            .returning(AgentRun.id)
        )
        async with self._session_factory() as session:
            agent_run_id: UUID = (await session.execute(stmt)).scalar_one()
            await session.commit()
        return agent_run_id

    async def _finish[T: OutputModel](
        self,
        agent_run_id: UUID,
        state: _State[T],
        status: AgentRunStatus,
        output: T | None,
    ) -> None:
        stmt = (
            update(AgentRun)
            .where(AgentRun.id == agent_run_id)
            .values(
                status=status.value,
                steps=state.steps,
                final_output=output.model_dump(mode="json") if output is not None else None,
                n_steps=state.n_steps,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                cost_estimate=state.cost,
                finished_at=datetime.now(UTC),
            )
        )
        async with self._session_factory() as session:
            await session.execute(stmt)
            await session.commit()


async def _within[R](deadline: float, call: Awaitable[R]) -> R:
    """Chờ `call` tới `deadline` (giờ của event loop); quá giờ → `_OutOfTime`."""
    try:
        async with asyncio.timeout_at(deadline) as scope:
            return await call
    except TimeoutError:
        if scope.expired():
            raise _OutOfTime from None
        raise


def _check_submit[T: OutputModel](
    submit: SubmitTool[T], payload: Any
) -> tuple[T | None, list[ToolError]]:
    try:
        value = submit.model.model_validate(payload)
    except ValidationError as exc:
        return None, [_invalid_arguments(exc, submit.name)]
    return value, list(submit.check(value))


def _invalid_arguments(exc: ValidationError, tool_name: str) -> ToolError:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors(include_input=False, include_url=False)
    )
    return ToolError(
        "INVALID_ARGUMENTS",
        f"Invalid arguments: {problems}.",
        f"Call {tool_name} again with arguments that match its input schema.",
    )


def _error_content(errors: Sequence[ToolError]) -> str:
    payload: dict[str, Any] = {"error": asdict(errors[0])}
    if len(errors) > 1:
        payload["other_errors"] = [asdict(e) for e in errors[1:]]
    return _truncate(json.dumps(payload, ensure_ascii=False))


def _truncate(content: str) -> str:
    if len(content) <= TOOL_OUTPUT_LIMIT:
        return content
    return content[:TOOL_OUTPUT_LIMIT] + TRUNCATED_MARK


def _tool_result(call: _Call) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": call.use.get("id"),
        "content": call.content,
    }
    if call.error_code is not None:
        block["is_error"] = True
    return block


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _entry(response: TurnResponse) -> dict[str, Any]:
    return {
        "content": [dict(b) for b in response.content],
        "stop_reason": response.stop_reason,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "model": response.model,
    }


def _entry_turn(entry: Mapping[str, Any]) -> TurnResponse | None:
    """Lượt từ entry cache, hoặc None nếu entry hỏng."""
    content = entry.get("content")
    stop_reason = entry.get("stop_reason")
    input_tokens = entry.get("input_tokens")
    output_tokens = entry.get("output_tokens")
    model = entry.get("model")
    if not (
        isinstance(content, list)
        and all(isinstance(b, dict) for b in content)
        and isinstance(stop_reason, str)
        and isinstance(input_tokens, int)
        and isinstance(output_tokens, int)
        and isinstance(model, str)
    ):
        return None
    return TurnResponse(
        content=tuple(content),
        stop_reason=stop_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    )
