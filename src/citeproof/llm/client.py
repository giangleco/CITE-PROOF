"""LLMClient: cache theo chế độ, validate schema, retry kèm phản hồi lỗi (07a §4.4.2, §4.7.2)."""

import logging
import time
from collections.abc import Mapping

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


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        cache: CacheStore,
        logger: CallLogger,
        *,
        mode: CacheMode = CacheMode.READ_WRITE,
        prices: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._logger = logger
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
    ) -> LLMResult[T]:
        validate_namespace(ctx.namespace, ctx.document_id)
        output_schema = schema.model_json_schema()
        messages: list[dict[str, str]] = [{"role": "user", "content": parts.user}]
        key = worker_cache_key(
            namespace=ctx.namespace,
            provider=self._provider.name,
            model=model,
            task=parts.task,
            prompt_version=parts.prompt_version,
            schema_version=schema.SCHEMA_VERSION,
            inputs={"system": parts.system, "messages": messages},
            attempt=1,
        )
        if self._mode is not CacheMode.FRESH:
            cached = await self._cache.get(key)
            if cached is not None:
                try:
                    value = schema.model_validate(cached["value"])
                except (KeyError, TypeError, ValidationError):
                    if self._mode is CacheMode.REPLAY_ONLY:
                        raise CacheEntryInvalidError(key) from None
                    logger.warning("llm_cache_entry_invalid", extra={"task": parts.task})
                    await self._cache.delete(key)
                else:
                    await self._log(parts, ctx, model, key, 0, 0, 0, cache_hit=True)
                    return LLMResult(value, True, key, 0, 0)
            elif self._mode is CacheMode.REPLAY_ONLY:
                raise CacheMissError(key)

        for _ in range(max_attempts):
            request = ProviderRequest(
                model=model,
                system=parts.system,
                messages=tuple(dict(m) for m in messages),
                output_schema=output_schema,
                max_tokens=max_tokens,
            )
            started = time.perf_counter()
            response = await self._provider.complete(request)
            latency_ms = round((time.perf_counter() - started) * 1000)
            await self._log(
                parts,
                ctx,
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
                    await self._cache.put(
                        key,
                        CacheEntryMeta(
                            ctx.namespace,
                            ctx.document_id,
                            parts.task,
                            model,
                            parts.prompt_version,
                            schema.SCHEMA_VERSION,
                        ),
                        {"value": value_or_none.model_dump(mode="json")},
                    )
                return LLMResult(
                    value_or_none, False, key, response.input_tokens, response.output_tokens
                )

            messages.append({"role": "assistant", "content": response.text})
            messages.append({"role": "user", "content": _feedback(problems)})

        raise LLMOutputError(f"{parts.task}: output invalid after {max_attempts} attempts")

    async def _log(
        self,
        parts: PromptParts,
        ctx: CallContext,
        model: str,
        key: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        *,
        cache_hit: bool,
    ) -> None:
        cost = estimate_cost(model, input_tokens, output_tokens, self._prices)
        await self._logger.log(
            LLMCallRecord(
                run_id=ctx.run_id,
                stage=ctx.stage,
                task=parts.task,
                provider=self._provider.name,
                model=model,
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
