"""LLMClient: cache theo chế độ, validate schema, retry kèm phản hồi lỗi (07a §4.4.2, §4.7.2)."""

import logging
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from citeproof.core.errors import LLMOutputError
from citeproof.llm.cache import (
    CacheEntryMeta,
    CacheStore,
    validate_namespace,
    worker_cache_key,
)
from citeproof.llm.calls import CallLogger, LLMCallRecord, estimate_cost
from citeproof.llm.context import PromptParts
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.types import (
    CacheEntryInvalidError,
    CacheMissError,
    CacheMode,
    CallContext,
    LLMResult,
    OutputModel,
    ProviderRequest,
)

logger = logging.getLogger(__name__)

_BAD_STOP_REASONS = frozenset({"max_tokens", "refusal"})


def _feedback(errors: list[str]) -> str:
    return "SCHEMA_INVALID:\n" + "\n".join(f"- {line}" for line in errors)


def _entry_value[T: OutputModel](schema: type[T], entry: Mapping[str, Any]) -> T | None:
    """Giá trị đã validate từ entry cache, hoặc None nếu entry hỏng."""
    try:
        return schema.model_validate(entry["value"])
    except (KeyError, TypeError, ValidationError):
        return None


def _entry_model(entry: Mapping[str, Any], requested: str) -> str:
    """Model đã trả lời entry; entry cũ (chưa ghi model) coi như model đã yêu cầu."""
    served = entry.get("model")
    return served if isinstance(served, str) and served else requested


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        cache: CacheStore,
        call_logger: CallLogger,
        *,
        mode: CacheMode = CacheMode.READ_WRITE,
        prices: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._call_logger = call_logger
        self._mode = mode
        self._prices: Mapping[str, tuple[float, float]] = prices or {}

    async def complete_structured[T: OutputModel](
        self,
        *,
        parts: PromptParts,
        schema: type[T],
        model: str,
        ctx: CallContext,
        max_tokens: int = 4096,
        max_attempts: int = 2,
        effort: str | None = None,
    ) -> LLMResult[T]:
        validate_namespace(ctx.namespace, ctx.document_id)
        output_schema = schema.model_json_schema()
        messages: list[dict[str, str]] = [{"role": "user", "content": parts.user}]
        inputs: dict[str, Any] = {"system": parts.system, "messages": messages}
        if effort is not None:  # không có effort thì khóa giữ nguyên như trước
            inputs["effort"] = effort
        key = worker_cache_key(
            namespace=ctx.namespace,
            provider=self._provider.name,
            model=model,
            task=parts.task,
            prompt_version=parts.prompt_version,
            schema_version=schema.SCHEMA_VERSION,
            inputs=inputs,
            attempt=1,
        )
        if self._mode is not CacheMode.FRESH:
            cached = await self._cache.get(key)
            if cached is not None:
                value = _entry_value(schema, cached)
                if value is None:
                    if self._mode is CacheMode.REPLAY_ONLY:
                        raise CacheEntryInvalidError(key)
                    self._warn_invalid_entry(parts, key)
                    await self._cache.delete(key)
                else:
                    served = _entry_model(cached, model)
                    await self._log(parts, ctx, served, model, key, 0, 0, 0, cache_hit=True)
                    return LLMResult(value, True, key, 0, 0, served)
            elif self._mode is CacheMode.REPLAY_ONLY:
                raise CacheMissError(key)

        for _ in range(max_attempts):
            request = ProviderRequest(
                model=model,
                system=parts.system,
                messages=tuple(dict(m) for m in messages),
                output_schema=output_schema,
                max_tokens=max_tokens,
                effort=effort,
            )
            started = time.perf_counter()
            response = await self._provider.complete(request)
            latency_ms = round((time.perf_counter() - started) * 1000)
            served = response.model or model
            await self._log(
                parts,
                ctx,
                served,
                model,
                key,
                response.input_tokens,
                response.output_tokens,
                latency_ms,
                cache_hit=False,
            )

            problems: list[str] = []
            value_or_none: T | None = None
            if response.stop_reason in _BAD_STOP_REASONS:
                problems.append(f"stop_reason={response.stop_reason}")
            try:
                value_or_none = schema.model_validate_json(response.text)
            except ValidationError as exc:
                for err in exc.errors(include_input=False, include_url=False):
                    loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                    problems.append(f"{loc}: {err['msg']}")

            if value_or_none is not None and not problems:
                if self._mode is CacheMode.READ_WRITE:
                    stored = await self._cache.put(
                        key,
                        CacheEntryMeta(
                            ctx.namespace,
                            ctx.document_id,
                            parts.task,
                            model,
                            parts.prompt_version,
                            schema.SCHEMA_VERSION,
                        ),
                        {"value": value_or_none.model_dump(mode="json"), "model": served},
                    )
                    # Lời gọi đồng thời cùng khoá: entry có trước thắng, để kết quả
                    # trả về bây giờ trùng với kết quả phát lại sau này.
                    winner = _entry_value(schema, stored)
                    if winner is None:
                        self._warn_invalid_entry(parts, key)
                    else:
                        value_or_none = winner
                        served = _entry_model(stored, model)
                return LLMResult(
                    value_or_none,
                    False,
                    key,
                    response.input_tokens,
                    response.output_tokens,
                    served,
                )

            # API từ chối lượt assistant rỗng (ví dụ khi model từ chối trước khi viết gì).
            if response.text.strip():
                messages.append({"role": "assistant", "content": response.text})
            messages.append({"role": "user", "content": _feedback(problems)})

        raise LLMOutputError(f"{parts.task}: output invalid after {max_attempts} attempts")

    def _warn_invalid_entry(self, parts: PromptParts, key: str) -> None:
        logger.warning("llm_cache_entry_invalid", extra={"task": parts.task, "cache_key": key})

    async def _log(
        self,
        parts: PromptParts,
        ctx: CallContext,
        served: str,
        requested: str,
        key: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        *,
        cache_hit: bool,
    ) -> None:
        # Model dự phòng chưa có trong bảng giá thì tính theo giá model đã yêu cầu.
        price_model = served if served in self._prices else requested
        cost = estimate_cost(price_model, input_tokens, output_tokens, self._prices)
        await self._call_logger.log(
            LLMCallRecord(
                run_id=ctx.run_id,
                stage=ctx.stage,
                task=parts.task,
                provider=self._provider.name,
                model=served,
                prompt_version=parts.prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_estimate=cost,
                cache_hit=cache_hit,
                cache_key=key,
                cache_namespace=ctx.namespace,
            )
        )
