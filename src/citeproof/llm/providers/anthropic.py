"""Provider Anthropic (ADR-006, spec §8.1, §9): structured output cho worker, lượt agent.

Không gửi tham số sampling, `thinking` hay `tool_choice`. Lỗi SDK được đổi sang lỗi
CiteProof có thông điệp chỉ gồm tên lớp lỗi và mã HTTP, không chứa prompt hay đầu ra.

Lời gọi worker dùng luồng (`messages.stream` rồi `get_final_message()`): W5 (Sonnet, effort
cao, 16 000 token) có thể chạy lâu hơn timeout của lời gọi không luồng, còn với luồng timeout
chỉ áp cho khoảng lặng giữa hai sự kiện. Lỗi gửi giữa luồng đến với mã HTTP 200 (yêu cầu đã
được nhận) và được coi là lỗi tạm thời.

Lượt agent (`complete_turn`, ADR-010) cũng chạy bằng luồng, luôn qua `messages.stream` thường,
không bao giờ qua đường beta có fallback phía server (QĐ-36: agent không dùng fallback). Công
cụ gửi kèm `strict: true`; khối nội dung trả về (kể cả thinking có chữ ký) được giữ nguyên để
AgentRunner gửi lại ở lượt sau.
"""

from collections.abc import Awaitable, Mapping
from typing import Any, Literal, cast, get_args

import anthropic
import httpx2
from anthropic import Omit, omit
from anthropic.types import (
    Message,
    MessageParam,
    OutputConfigParam,
    TextBlock,
    ToolParam,
    Usage,
)
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlock,
)

from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import ProviderRequest, ProviderResponse, TurnRequest, TurnResponse

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
        message = await _mapped(self._create(request, effort))
        # Chỉ lấy khối text; bỏ khối thinking và khối đánh dấu chuyển sang model dự phòng.
        text = "".join(
            block.text for block in message.content if isinstance(block, TextBlock | BetaTextBlock)
        )
        return ProviderResponse(
            text=text,
            input_tokens=_input_tokens(message.usage),
            output_tokens=message.usage.output_tokens,
            stop_reason=message.stop_reason or "",
            model=message.model,
        )

    async def complete_turn(self, request: TurnRequest) -> TurnResponse:
        effort = _checked_effort(request.effort)
        message = await _mapped(self._turn(request, effort))
        # Khối có khóa None (ví dụ `citations` của text) bỏ khóa đó; `input` của tool_use giữ
        # nguyên vì giá trị null là đối số thật của model.
        blocks = cast("list[Mapping[str, Any]]", message.to_dict()["content"])
        content = tuple(_without_none(block) for block in blocks)
        return TurnResponse(
            content=content,
            stop_reason=message.stop_reason or "",
            input_tokens=_input_tokens(message.usage),
            output_tokens=message.usage.output_tokens,
            model=message.model,
        )

    async def aclose(self) -> None:
        """Đóng client HTTP của SDK; gọi một lần khi worker dừng (`open_deps`)."""
        await self._client.close()

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
            async with self._client.beta.messages.stream(
                model=request.model,
                max_tokens=request.max_tokens,
                system=[
                    {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
                ],
                messages=cast("list[BetaMessageParam]", messages),
                output_config=beta_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as beta_stream:
                return await beta_stream.get_final_message()
        config: OutputConfigParam = {"format": {"type": "json_schema", "schema": schema}}
        if effort is not None:
            config["effort"] = effort
        async with self._client.messages.stream(
            model=request.model,
            max_tokens=request.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=cast("list[MessageParam]", messages),
            output_config=config,
        ) as stream:
            return await stream.get_final_message()

    async def _turn(self, request: TurnRequest, effort: Effort | None) -> Message:
        tools: list[ToolParam] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": strict_output_schema(tool.input_schema),
                "strict": True,
            }
            for tool in request.tools
        ]
        config: OutputConfigParam | Omit = {"effort": effort} if effort is not None else omit
        async with self._client.messages.stream(
            model=request.model,
            max_tokens=request.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=cast("list[MessageParam]", [dict(m) for m in request.messages]),
            tools=tools,
            output_config=config,
        ) as stream:
            return await stream.get_final_message()


async def _mapped[T](call: Awaitable[T]) -> T:
    """Đổi lỗi SDK sang lỗi CiteProof; thông điệp chỉ có tên lớp lỗi và mã HTTP."""
    try:
        return await call
    except anthropic.RateLimitError as exc:
        raise RetryableError(_describe(exc, exc.status_code)) from exc
    except anthropic.APIConnectionError as exc:  # gồm cả APITimeoutError
        raise RetryableError(_describe(exc, None)) from exc
    except anthropic.APIStatusError as exc:
        # < 400: lỗi giữa luồng (ví dụ quá tải) sau khi máy chủ đã trả 200.
        if exc.status_code >= 500 or exc.status_code < 400:
            raise RetryableError(_describe(exc, exc.status_code)) from exc
        raise ExternalServiceError(_describe(exc, exc.status_code)) from exc
    except httpx2.TransportError as exc:
        # SDK chỉ bọc lỗi mạng khi mở yêu cầu; lỗi khi đọc luồng (ReadTimeout, ReadError,
        # RemoteProtocolError) đến nguyên dạng từ httpx2.
        raise RetryableError(_describe(exc, None)) from exc


def _input_tokens(usage: Usage | Any) -> int:
    """Token đọc/ghi prompt cache cũng là token đầu vào của lượt gọi."""
    return int(
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0)
    )


def _without_none(block: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in block.items() if value is not None}


def _checked_effort(effort: str | None) -> Effort | None:
    if effort is None:
        return None
    if effort not in _EFFORTS:
        raise ValueError(f"effort không hợp lệ: {effort!r}")
    return cast(Effort, effort)


def _describe(exc: Exception, status: int | None) -> str:
    return f"anthropic {type(exc).__name__} status={status}"
