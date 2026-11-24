"""Provider Anthropic cho worker (ADR-006, spec §8.1): structured output qua SDK chính thức.

Không gửi tham số sampling, `thinking` hay `tool_choice`. Lỗi SDK được đổi sang lỗi
CiteProof có thông điệp chỉ gồm tên lớp lỗi và mã HTTP, không chứa prompt hay đầu ra.
"""

from typing import Literal, cast, get_args

import anthropic
from anthropic.types import Message, MessageParam, OutputConfigParam, TextBlock
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlock,
)

from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import ProviderRequest, ProviderResponse

type Effort = Literal["low", "medium", "high", "xhigh", "max"]

_EFFORTS = frozenset(get_args(Effort.__value__))
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Fallback phía server chỉ có cho Sonnet và Opus; Haiku không có model dự phòng.
_FALLBACK_MODEL_PREFIXES = ("claude-sonnet", "claude-opus")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic, *, refusal_fallback: bool) -> None:
        self._client = client
        self._refusal_fallback = refusal_fallback

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        effort = _checked_effort(request.effort)
        try:
            message = await self._create(request, effort)
        except anthropic.RateLimitError as exc:
            raise RetryableError(_describe(exc, exc.status_code)) from exc
        except anthropic.APIConnectionError as exc:  # gồm cả APITimeoutError
            raise RetryableError(_describe(exc, None)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableError(_describe(exc, exc.status_code)) from exc
            raise ExternalServiceError(_describe(exc, exc.status_code)) from exc

        usage = message.usage
        # Token đọc/ghi prompt cache cũng là token đầu vào của lượt gọi.
        input_tokens = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
        )
        # Chỉ lấy khối text; bỏ khối thinking và khối đánh dấu chuyển sang model dự phòng.
        text = "".join(
            block.text for block in message.content if isinstance(block, TextBlock | BetaTextBlock)
        )
        return ProviderResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            stop_reason=message.stop_reason or "",
            model=message.model,
        )

    def _uses_fallback(self, model: str) -> bool:
        return self._refusal_fallback and model.startswith(_FALLBACK_MODEL_PREFIXES)

    async def _create(
        self, request: ProviderRequest, effort: Effort | None
    ) -> Message | BetaMessage:
        schema = strict_output_schema(request.output_schema)
        messages = [dict(m) for m in request.messages]
        if self._uses_fallback(request.model):
            beta_config: BetaOutputConfigParam = {
                "format": {"type": "json_schema", "schema": schema}
            }
            if effort is not None:
                beta_config["effort"] = effort
            return await self._client.beta.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=[
                    {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
                ],
                messages=cast("list[BetaMessageParam]", messages),
                output_config=beta_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        config: OutputConfigParam = {"format": {"type": "json_schema", "schema": schema}}
        if effort is not None:
            config["effort"] = effort
        return await self._client.messages.create(
            model=request.model,
            max_tokens=request.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=cast("list[MessageParam]", messages),
            output_config=config,
        )


def _checked_effort(effort: str | None) -> Effort | None:
    if effort is None:
        return None
    if effort not in _EFFORTS:
        raise ValueError(f"effort không hợp lệ: {effort!r}")
    return cast(Effort, effort)


def _describe(exc: anthropic.APIError, status: int | None) -> str:
    return f"anthropic {type(exc).__name__} status={status}"
