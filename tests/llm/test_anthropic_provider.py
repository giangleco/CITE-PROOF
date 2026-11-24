from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import anthropic
import httpx2
import pytest
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from pydantic import Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.core.settings import Settings
from citeproof.llm.cache import PgCacheStore
from citeproof.llm.calls import PgCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.factory import make_llm_client
from citeproof.llm.providers.anthropic import AnthropicProvider
from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import OutputModel, ProviderRequest

SYSTEM = "You are a careful parser."
PROMPT = "PROMPT-SECRET: Nguyễn Văn A (2020) đã chứng minh điều này."
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FORBIDDEN = {"temperature", "top_p", "top_k", "tool_choice", "thinking"}


class Out(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "out/1"
    a: int = Field(ge=0)


S = Out.model_json_schema()


def payload(
    *blocks: dict[str, Any],
    model: str = "claude-haiku-5-5",
    stop_reason: str = "end_turn",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": list(blocks) or [{"type": "text", "text": '{"a":1}'}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


class _Endpoint:
    """Giả `messages.create`: ghi kwargs, trả Message dựng từ payload hoặc ném lỗi."""

    def __init__(self, parse: Callable[[Any], Any], result: dict[str, Any] | BaseException) -> None:
        self._parse = parse
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._parse(self._result)


class StubSDK:
    def __init__(self, result: dict[str, Any] | BaseException) -> None:
        self.messages = _Endpoint(Message.model_validate, result)
        self.beta = SimpleNamespace(messages=_Endpoint(BetaMessage.model_validate, result))


def make(
    result: dict[str, Any] | BaseException, *, fallback: bool = True
) -> tuple[AnthropicProvider, StubSDK]:
    stub = StubSDK(result)
    return AnthropicProvider(cast(anthropic.AsyncAnthropic, stub), refusal_fallback=fallback), stub


def request(model: str = "claude-haiku-5-5", effort: str | None = "low") -> ProviderRequest:
    return ProviderRequest(
        model=model,
        system=SYSTEM,
        messages=({"role": "user", "content": PROMPT},),
        output_schema=S,
        max_tokens=4000,
        effort=effort,
    )


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "x", "message": f"echo {PROMPT}"}}
    return cls(
        f"Error code: {code} - {PROMPT}", response=httpx2.Response(code, request=req), body=body
    )


async def test_request_shape_structured_output() -> None:
    provider, stub = make(payload())
    await provider.complete(request("claude-haiku-5-5", "low"))
    assert stub.beta.messages.calls == []
    (kwargs,) = stub.messages.calls
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["max_tokens"] == 4000
    assert kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": strict_output_schema(S)},
        "effort": "low",
    }
    assert kwargs["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"] == [{"role": "user", "content": PROMPT}]
    assert not (FORBIDDEN & kwargs.keys())
    assert "fallbacks" not in kwargs and "betas" not in kwargs


async def test_effort_omitted_when_none() -> None:
    provider, stub = make(payload())
    await provider.complete(request(effort=None))
    (kwargs,) = stub.messages.calls
    assert kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": strict_output_schema(S)}
    }


async def test_unknown_effort_is_rejected_before_the_call() -> None:
    provider, stub = make(payload())
    with pytest.raises(ValueError):
        await provider.complete(request(effort="extreme"))
    assert stub.messages.calls == []


async def test_text_blocks_joined_thinking_ignored() -> None:
    provider, _ = make(
        payload(
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": '{"a":'},
            {"type": "text", "text": "1}"},
        )
    )
    response = await provider.complete(request())
    assert response.text == '{"a":1}'
    assert response.stop_reason == "end_turn"


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-opus-5-5"])
async def test_refusal_fallback_for_sonnet_and_opus(model: str) -> None:
    provider, stub = make(payload(model=model))
    await provider.complete(request(model, "high"))
    assert stub.messages.calls == []
    (kwargs,) = stub.beta.messages.calls
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["model"] == model
    assert kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": strict_output_schema(S)},
        "effort": "high",
    }
    assert kwargs["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    assert not (FORBIDDEN & kwargs.keys())


async def test_no_fallback_for_haiku() -> None:
    provider, stub = make(payload())
    await provider.complete(request("claude-haiku-5-5"))
    assert len(stub.messages.calls) == 1
    assert stub.beta.messages.calls == []


async def test_fallback_disabled_by_setting() -> None:
    provider, stub = make(payload(model="claude-sonnet-5-5"), fallback=False)
    await provider.complete(request("claude-sonnet-5-5", "high"))
    assert stub.beta.messages.calls == []
    (kwargs,) = stub.messages.calls
    assert "fallbacks" not in kwargs and "betas" not in kwargs


async def test_usage_and_served_model() -> None:
    served = payload(
        {
            "type": "fallback",
            "from": {"model": "claude-sonnet-5-5"},
            "to": {"model": "claude-sonnet-5"},
            "trigger": {"type": "refusal", "category": "cyber"},
        },
        {"type": "text", "text": '{"a":2}'},
        model="claude-sonnet-5",
        usage={
            "input_tokens": 10,
            "output_tokens": 7,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 40,
        },
    )
    provider, _ = make(served)
    response = await provider.complete(request("claude-sonnet-5-5", "high"))
    assert response.input_tokens == 350
    assert response.output_tokens == 7
    assert response.model == "claude-sonnet-5"
    assert response.text == '{"a":2}'


async def test_missing_cache_usage_counts_as_zero() -> None:
    provider, _ = make(payload(usage={"input_tokens": 12, "output_tokens": 3}))
    response = await provider.complete(request())
    assert (response.input_tokens, response.output_tokens) == (12, 3)


async def test_refusal_stop_reason_is_passed_through() -> None:
    provider, _ = make(payload({"type": "text", "text": ""}, stop_reason="refusal"))
    response = await provider.complete(request())
    assert response.stop_reason == "refusal"


async def test_rate_limit_maps_to_retryable() -> None:
    provider, _ = make(_status_error(anthropic.RateLimitError, 429))
    with pytest.raises(RetryableError) as info:
        await provider.complete(request())
    message = str(info.value)
    assert message == "anthropic RateLimitError status=429"
    assert PROMPT not in message and SYSTEM not in message


@pytest.mark.parametrize(
    ("cls", "code"),
    [(anthropic.InternalServerError, 500), (anthropic.APIStatusError, 529)],
)
async def test_server_errors_map_to_retryable(
    cls: type[anthropic.APIStatusError], code: int
) -> None:
    provider, _ = make(_status_error(cls, code))
    with pytest.raises(RetryableError) as info:
        await provider.complete(request())
    assert str(info.value) == f"anthropic {cls.__name__} status={code}"


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
        anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")),
    ],
)
async def test_connection_errors_map_to_retryable(error: anthropic.APIConnectionError) -> None:
    provider, _ = make(error)
    with pytest.raises(RetryableError) as info:
        await provider.complete(request())
    assert str(info.value) == f"anthropic {type(error).__name__} status=None"


@pytest.mark.parametrize(
    ("cls", "code"),
    [(anthropic.BadRequestError, 400), (anthropic.AuthenticationError, 401)],
)
async def test_client_errors_map_to_external_error(
    cls: type[anthropic.APIStatusError], code: int
) -> None:
    provider, _ = make(_status_error(cls, code))
    with pytest.raises(ExternalServiceError) as info:
        await provider.complete(request())
    assert not isinstance(info.value, RetryableError)
    assert str(info.value) == f"anthropic {cls.__name__} status={code}"
    assert PROMPT not in str(info.value)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_make_llm_client_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_ANTHROPIC_API_KEY", raising=False)
    assert make_llm_client(_settings(), async_sessionmaker()) is None
    assert make_llm_client(_settings(anthropic_api_key=""), async_sessionmaker()) is None


def test_make_llm_client_with_key_uses_anthropic_provider() -> None:
    settings = _settings(
        anthropic_api_key="sk-test",
        anthropic_timeout_seconds=30.0,
        anthropic_max_retries=5,
        anthropic_refusal_fallback=False,
    )
    client = make_llm_client(settings, async_sessionmaker())
    assert isinstance(client, LLMClient)
    provider = client._provider
    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"
    assert provider._refusal_fallback is False
    sdk = provider._client
    assert sdk.api_key == "sk-test"
    assert sdk.timeout == 30.0
    assert sdk.max_retries == 5
    assert client._prices == settings.llm_prices
    assert isinstance(client._cache, PgCacheStore)
    assert isinstance(client._call_logger, PgCallLogger)


async def test_aclose_closes_sdk_client() -> None:
    sdk = anthropic.AsyncAnthropic(api_key="sk-test")
    provider = AnthropicProvider(sdk, refusal_fallback=True)
    assert not sdk.is_closed()
    await provider.aclose()
    assert sdk.is_closed()
