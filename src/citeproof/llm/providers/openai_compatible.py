"""Provider OpenAI-compatible (spec §5.3): Chat Completions, gọi không luồng.

Không gửi tham số sampling. Lỗi SDK được đổi sang lỗi CiteProof có thông điệp chỉ gồm định
danh, tên lớp lỗi và mã HTTP, không chứa prompt hay đầu ra. `name` là định danh provider
(`openai_compatible:<host>`) và là một phần của cache key.
"""

import json
import logging
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, Literal, cast

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import (
    ProviderRequest,
    ProviderResponse,
    TurnRequest,
    TurnResponse,
)

logger = logging.getLogger(__name__)

type StructuredOutput = Literal["json_schema", "json_object", "none"]

# OpenAI yêu cầu tên schema khớp `^[a-zA-Z0-9_-]+$`; ProviderRequest không mang tên tác vụ.
_SCHEMA_NAME = "output"
_FINISH_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def map_finish_reason(reason: str | None, *, refused: bool) -> str:
    """`finish_reason` của Chat Completions sang `stop_reason` nội bộ."""
    if refused:
        return "refusal"
    if reason is None:
        return ""
    return _FINISH_REASONS.get(reason, reason)


def to_chat_messages(system: str, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Hội thoại khối kiểu Anthropic sang tin nhắn Chat Completions (spec §5.3).

    Khối `thinking` và khối lạ bị bỏ. Trong tin user, mỗi `tool_result` thành một tin `tool`
    đặt trước tin user chứa phần text cùng lượt.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = [b for b in content or () if isinstance(b, Mapping)]
        texts = [str(b.get("text", "")) for b in blocks if b.get("type") == "text"]
        if role == "user":
            out.extend(
                {
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id"),
                    "content": _tool_content(b.get("content")),
                }
                for b in blocks
                if b.get("type") == "tool_result"
            )
            if texts:
                out.append({"role": "user", "content": "\n\n".join(texts)})
            continue
        calls = [
            {
                "id": b.get("id"),
                "type": "function",
                "function": {
                    "name": b.get("name"),
                    "arguments": json.dumps(b.get("input"), ensure_ascii=False),
                },
            }
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        if not texts and not calls:
            continue
        entry: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
        if calls:
            entry["tool_calls"] = calls
        out.append(entry)
    return out


def _tool_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, Mapping) and b.get("type") == "text"
        )
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def from_chat_message(message: Any) -> tuple[dict[str, Any], ...]:
    """Tin nhắn assistant của Chat Completions sang khối nội dung nội bộ.

    `arguments` không phải JSON object hợp lệ -> `input = {}`: công cụ trả lỗi kiểm tra đầu
    vào cho agent như với đối số sai khác.
    """
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls or ():
        function = getattr(call, "function", None)
        if function is None:
            continue
        try:
            parsed = json.loads(function.arguments)
        except ValueError:
            parsed = None
        blocks.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": function.name,
                "input": parsed if isinstance(parsed, dict) else {},
            }
        )
    return tuple(blocks)


class OpenAICompatibleProvider:
    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        identity: str,
        structured_output: StructuredOutput,
        send_reasoning_effort: bool,
    ) -> None:
        self._client = client
        self.name = identity
        self._structured_output = structured_output
        self._send_reasoning_effort = send_reasoning_effort
        self._warned_missing_usage = False

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        completion = await self._mapped(self._create(request))
        # Gateway lỗi có thể trả 200 với thân không phải ChatCompletion hoặc `choices` rỗng/null;
        # thông điệp không chứa thân phản hồi.
        if not isinstance(completion, ChatCompletion) or not completion.choices:
            raise ExternalServiceError(f"{self.name} empty_choices")
        choice = completion.choices[0]
        message = choice.message
        refused = bool(message.refusal)
        usage = completion.usage
        if usage is None and not self._warned_missing_usage:
            self._warned_missing_usage = True
            logger.warning("%s không trả usage; đếm token là 0", self.name)
        return ProviderResponse(
            text=message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            stop_reason=map_finish_reason(choice.finish_reason, refused=refused),
            model=completion.model or request.model,
        )

    async def complete_turn(self, request: TurnRequest) -> TurnResponse:
        completion = await self._mapped(self._create_turn(request))
        if not isinstance(completion, ChatCompletion) or not completion.choices:
            raise ExternalServiceError(f"{self.name} empty_choices")
        choice = completion.choices[0]
        usage = completion.usage
        if usage is None and not self._warned_missing_usage:
            self._warned_missing_usage = True
            logger.warning("%s không trả usage; đếm token là 0", self.name)
        return TurnResponse(
            content=from_chat_message(choice.message),
            stop_reason=map_finish_reason(
                choice.finish_reason, refused=bool(choice.message.refusal)
            ),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=completion.model or request.model,
        )

    async def aclose(self) -> None:
        """Đóng client HTTP của SDK; gọi một lần khi worker dừng (`open_deps`)."""
        await self._client.close()

    async def _create(self, request: ProviderRequest) -> ChatCompletion:
        system = request.system
        response_format: Any = openai.omit
        if self._structured_output == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": _SCHEMA_NAME,
                    "schema": strict_output_schema(request.output_schema),
                    "strict": False,
                },
            }
        else:
            if self._structured_output == "json_object":
                response_format = {"type": "json_object"}
            # Không có ràng buộc phía server: nêu cả ràng buộc (minimum, ...) bằng schema gốc.
            shown = json.dumps(request.output_schema, ensure_ascii=False)
            system = f"{system}\n\nJSON schema của đầu ra:\n{shown}"
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(dict(m) for m in request.messages)
        return await self._client.chat.completions.create(
            model=request.model,
            messages=cast("list[ChatCompletionMessageParam]", messages),
            max_completion_tokens=request.max_tokens,
            response_format=response_format,
            reasoning_effort=self._effort(request.effort),
        )

    async def _create_turn(self, request: TurnRequest) -> ChatCompletion:
        tools: Any = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": strict_output_schema(tool.input_schema),
                },
            }
            for tool in request.tools
        ] or openai.omit
        return await self._client.chat.completions.create(
            model=request.model,
            messages=cast(
                "list[ChatCompletionMessageParam]",
                to_chat_messages(request.system, request.messages),
            ),
            max_completion_tokens=request.max_tokens,
            tools=tools,
            reasoning_effort=self._effort(request.effort),
        )

    def _effort(self, effort: str | None) -> Any:
        return effort if self._send_reasoning_effort and effort is not None else openai.omit

    async def _mapped[T](self, call: Awaitable[T]) -> T:
        """Đổi lỗi SDK sang lỗi CiteProof; thông điệp chỉ có định danh, tên lớp lỗi, mã HTTP."""
        try:
            return await call
        except openai.RateLimitError as exc:
            raise RetryableError(self._describe(exc, exc.status_code)) from exc
        except openai.APIConnectionError as exc:  # gồm cả APITimeoutError
            raise RetryableError(self._describe(exc, None)) from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableError(self._describe(exc, exc.status_code)) from exc
            raise ExternalServiceError(self._describe(exc, exc.status_code)) from exc
        except openai.APIError as exc:
            # Lỗi SDK khác (APIResponseValidationError...): str(exc) có thể chứa thân phản hồi.
            raise ExternalServiceError(self._describe(exc, None)) from exc

    def _describe(self, exc: Exception, status: int | None) -> str:
        return f"{self.name} {type(exc).__name__} status={status}"
