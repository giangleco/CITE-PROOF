"""Hợp đồng chung của `LLMProvider` (spec §5.5), chạy cho từng provider trên SDK giả.

Mỗi backend dựng provider thật trên SDK giả (stub Anthropic, httpx `MockTransport` cho OpenAI)
và diễn kịch bản trung lập; test không gọi mạng và không biết provider nào đang chạy.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

import anthropic
import httpx
import httpx2
import pytest

from citeproof.core import defaults
from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.core.settings import Settings
from citeproof.llm.factory import make_provider
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.types import ProviderRequest, ToolSpec, TurnRequest
from tests.llm import test_anthropic_provider as anth
from tests.llm import test_openai_compatible_provider as oai
from tests.llm import test_openai_compatible_turns as oai_turns

PROMPT = "PROMPT-SECRET: Nguyễn Văn A (2020) đã chứng minh điều này."
SYSTEM = "SYSTEM-SECRET: you are a careful parser."
TOOL_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


class Backend(Protocol):
    provider: LLMProvider

    def worker(self, *, stop: str, text: str = '{"a":1}') -> None: ...
    def tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None: ...
    def final_text(self, text: str) -> None: ...
    def fail(self, status: int | None) -> None:
        """Lỗi HTTP `status`; None: mất kết nối."""
        ...

    def last_request(self) -> str:
        """Phần thân yêu cầu gần nhất, dạng chuỗi JSON."""
        ...

    async def aclose(self) -> None: ...


class AnthropicBackend:
    def __init__(self) -> None:
        self.provider, self._stub = anth.make(anth.payload())

    def worker(self, *, stop: str, text: str = '{"a":1}') -> None:
        self._stub.messages.result = anth.payload({"type": "text", "text": text}, stop_reason=stop)

    def tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
        self._stub.messages.result = anth.payload(block, stop_reason="tool_use")

    def final_text(self, text: str) -> None:
        self._stub.messages.result = anth.payload({"type": "text", "text": text})

    def fail(self, status: int | None) -> None:
        if status is None:
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            self._stub.messages.result = anthropic.APIConnectionError(request=req)
            return
        classes: dict[int, type[anthropic.APIStatusError]] = {
            400: anthropic.BadRequestError,
            401: anthropic.AuthenticationError,
            429: anthropic.RateLimitError,
            500: anthropic.InternalServerError,
        }
        self._stub.messages.result = anth._status_error(classes[status], status)

    def last_request(self) -> str:
        kwargs = (self._stub.messages.calls or self._stub.beta.messages.calls)[-1]
        return json.dumps(kwargs, default=str)

    async def aclose(self) -> None:
        return None


class OpenAIBackend:
    def __init__(self) -> None:
        self._respond: Callable[[], httpx.Response] = lambda: httpx.Response(
            200, json=oai.completion()
        )
        self._lost = False

        def handler(_req: httpx.Request) -> httpx.Response:
            if self._lost:
                raise httpx.ConnectError("connection refused")
            return self._respond()

        self.provider, self._bodies, self._http = oai.make(handler)

    def _reply(self, body: dict[str, Any]) -> None:
        self._respond = lambda: httpx.Response(200, json=body)

    def worker(self, *, stop: str, text: str = '{"a":1}') -> None:
        finish = {"end_turn": "stop", "max_tokens": "length", "refusal": "content_filter"}[stop]
        self._reply(oai.completion(content=text, finish_reason=finish))

    def tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        message = oai_turns.tool_call_message([(call_id, name, args)])
        self._reply(oai_turns.chat_body(message))

    def final_text(self, text: str) -> None:
        self._reply(oai_turns.chat_body({"content": text}, finish="stop"))

    def fail(self, status: int | None) -> None:
        if status is None:
            self._lost = True
        else:
            self._respond = lambda: httpx.Response(status, json={"error": PROMPT})

    def last_request(self) -> str:
        return json.dumps(self._bodies[-1])

    async def aclose(self) -> None:
        await self._http.aclose()


BACKENDS: dict[str, Callable[[], Backend]] = {
    "anthropic": AnthropicBackend,
    "openai_compatible": OpenAIBackend,
}


@pytest.fixture(params=sorted(BACKENDS))
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    instance = BACKENDS[request.param]()
    yield instance
    await instance.aclose()


def worker_request() -> ProviderRequest:
    return ProviderRequest(
        model="m",
        system=SYSTEM,
        messages=({"role": "user", "content": PROMPT},),
        output_schema=oai.S,
        max_tokens=500,
        effort=None,
    )


def turn_request(*extra: dict[str, Any]) -> TurnRequest:
    return TurnRequest(
        model="m",
        system=SYSTEM,
        messages=({"role": "user", "content": [{"type": "text", "text": PROMPT}]}, *extra),
        tools=(ToolSpec("lookup", "Look up.", TOOL_SCHEMA),),
        max_tokens=500,
        effort=None,
    )


async def test_worker_json_text_and_tokens(backend: Backend) -> None:
    backend.worker(stop="end_turn")
    response = await backend.provider.complete(worker_request())
    assert json.loads(response.text) == {"a": 1}
    assert response.input_tokens > 0 and response.output_tokens > 0
    assert response.stop_reason == "end_turn"


@pytest.mark.parametrize("stop", ["end_turn", "max_tokens", "refusal"])
async def test_worker_stop_reason(backend: Backend, stop: str) -> None:
    backend.worker(stop=stop, text="")
    response = await backend.provider.complete(worker_request())
    assert response.stop_reason == stop


async def test_worker_request_has_no_sampling_params(backend: Backend) -> None:
    backend.worker(stop="end_turn")
    await backend.provider.complete(worker_request())
    sent = backend.last_request()
    for key in ("temperature", "top_p", "top_k"):
        assert f'"{key}"' not in sent


async def test_agent_tool_call_and_result_roundtrip(backend: Backend) -> None:
    backend.tool_call("call_1", "lookup", {"query": "attention"})
    first = await backend.provider.complete_turn(turn_request())
    assert first.stop_reason == "tool_use"
    (block,) = [b for b in first.content if b["type"] == "tool_use"]
    assert block["id"] == "call_1" and block["name"] == "lookup"
    assert block["input"] == {"query": "attention"}

    backend.final_text("done")
    second = await backend.provider.complete_turn(
        turn_request(
            {"role": "assistant", "content": list(first.content)},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "{}"}],
            },
        )
    )
    assert second.stop_reason == "end_turn"
    assert [b["text"] for b in second.content if b["type"] == "text"] == ["done"]
    assert second.input_tokens > 0
    assert "call_1" in backend.last_request()


@pytest.mark.parametrize("status", [429, 500, None])
async def test_retryable_errors(backend: Backend, status: int | None) -> None:
    backend.fail(status)
    with pytest.raises(RetryableError) as worker:
        await backend.provider.complete(worker_request())
    with pytest.raises(RetryableError) as turn:
        await backend.provider.complete_turn(turn_request())
    for info in (worker, turn):
        message = str(info.value)
        assert message.startswith(backend.provider.name)
        assert PROMPT not in message and SYSTEM not in message


@pytest.mark.parametrize("status", [400, 401])
async def test_client_errors_are_not_retryable(backend: Backend, status: int) -> None:
    backend.fail(status)
    with pytest.raises(ExternalServiceError) as worker:
        await backend.provider.complete(worker_request())
    with pytest.raises(ExternalServiceError) as turn:
        await backend.provider.complete_turn(turn_request())
    for info in (worker, turn):
        assert not isinstance(info.value, RetryableError)
        message = str(info.value)
        assert f"status={status}" in message
        assert PROMPT not in message and SYSTEM not in message


def _settings(kind: str) -> Settings:
    if kind == "anthropic":
        return Settings(_env_file=None, anthropic_api_key="sk-test")  # type: ignore[call-arg]
    models = {task: "gpt-x" for task in defaults.LLM_MODELS}
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_provider="openai_compatible",
        openai_base_url="http://api.example.test/v1",
        llm_models=models,
        llm_prices={"gpt-x": (1.0, 2.0)},
    )


@pytest.mark.parametrize("kind", sorted(BACKENDS))
async def test_name_is_non_empty_and_stable(kind: str) -> None:
    first, second = make_provider(_settings(kind)), make_provider(_settings(kind))
    assert first is not None and second is not None
    assert first.name and first.name == second.name
    assert first.name.split(":")[0] == kind
