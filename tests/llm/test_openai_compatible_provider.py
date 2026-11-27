import json
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import openai
import pytest
from pydantic import Field

from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.llm.providers.openai_compatible import OpenAICompatibleProvider, map_finish_reason
from citeproof.llm.types import OutputModel, ProviderRequest

IDENTITY = "openai_compatible:api.example.test"
SYSTEM = "SYSTEM-SECRET: you are a careful parser."
PROMPT = "PROMPT-SECRET: Nguyễn Văn A (2020) đã chứng minh điều này."


class Out(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "out/1"
    a: int = Field(ge=0)


S = Out.model_json_schema()


def completion(
    *,
    content: str | None = '{"a":1}',
    finish_reason: str = "stop",
    refusal: str | None = None,
    usage: dict[str, Any] | bool | None = True,
    model: str = "gpt-test-2026",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
    }
    if usage is True:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    elif usage:
        body["usage"] = usage
    return body


def make(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    structured_output: Any = "json_schema",
    send_reasoning_effort: bool = False,
) -> tuple[OpenAICompatibleProvider, list[dict[str, Any]], httpx.AsyncClient]:
    bodies: list[dict[str, Any]] = []

    def record(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return handler(req)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    sdk = openai.AsyncOpenAI(
        base_url="http://api.example.test/v1", api_key="unused", max_retries=0, http_client=http
    )
    provider = OpenAICompatibleProvider(
        sdk,
        identity=IDENTITY,
        structured_output=structured_output,
        send_reasoning_effort=send_reasoning_effort,
    )
    return provider, bodies, http


def ok(**kwargs: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(200, json=completion(**kwargs))


def request(effort: str | None = None) -> ProviderRequest:
    return ProviderRequest(
        model="gpt-test",
        system=SYSTEM,
        messages=({"role": "user", "content": PROMPT},),
        output_schema=S,
        max_tokens=321,
        effort=effort,
    )


async def test_name_is_identity() -> None:
    provider, _, http = make(ok())
    assert provider.name == IDENTITY
    await http.aclose()


async def test_request_shape_json_schema() -> None:
    provider, bodies, http = make(ok(), send_reasoning_effort=True)
    await provider.complete(request())
    body = bodies[0]
    assert body["model"] == "gpt-test"
    assert body["messages"][0] == {"role": "system", "content": SYSTEM}
    assert body["messages"][1] == {"role": "user", "content": PROMPT}
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is False
    assert fmt["json_schema"]["name"] == "output"
    assert fmt["json_schema"]["schema"]["properties"]["a"]["type"] == "integer"
    assert "minimum" not in fmt["json_schema"]["schema"]["properties"]["a"]
    assert body["max_completion_tokens"] == 321
    assert "max_tokens" not in body
    assert not {"temperature", "top_p", "top_k", "reasoning_effort"} & body.keys()
    await http.aclose()


async def test_json_object_mode_appends_schema_to_system() -> None:
    provider, bodies, http = make(ok(), structured_output="json_object")
    await provider.complete(request())
    body = bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    system = body["messages"][0]["content"]
    assert system.startswith(SYSTEM)
    assert json.dumps(S) in system
    await http.aclose()


async def test_none_mode_has_no_response_format() -> None:
    provider, bodies, http = make(ok(), structured_output="none")
    await provider.complete(request())
    assert "response_format" not in bodies[0]
    assert json.dumps(S) in bodies[0]["messages"][0]["content"]
    await http.aclose()


async def test_reasoning_effort_sent_only_when_enabled() -> None:
    provider, bodies, http = make(ok(), send_reasoning_effort=True)
    await provider.complete(request("high"))
    assert bodies[0]["reasoning_effort"] == "high"
    await provider.complete(request(None))
    assert "reasoning_effort" not in bodies[1]
    await http.aclose()

    off, off_bodies, http2 = make(ok(), send_reasoning_effort=False)
    await off.complete(request("high"))
    assert "reasoning_effort" not in off_bodies[0]
    await http2.aclose()


async def test_response_mapping() -> None:
    provider, _, http = make(ok())
    response = await provider.complete(request())
    assert response.text == '{"a":1}'
    assert response.input_tokens == 11
    assert response.output_tokens == 7
    assert response.model == "gpt-test-2026"
    assert response.stop_reason == "end_turn"
    await http.aclose()


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("length", "max_tokens"),
        ("content_filter", "refusal"),
        ("tool_calls", "tool_use"),
        ("stop", "end_turn"),
        ("weird", "weird"),
        (None, ""),
    ],
)
async def test_finish_reason_mapping(reason: str | None, expected: str) -> None:
    assert map_finish_reason(reason, refused=False) == expected
    provider, _, http = make(ok(finish_reason=reason or "stop"))
    if reason is not None:
        assert (await provider.complete(request())).stop_reason == expected
    await http.aclose()


async def test_refusal_message_maps_to_refusal() -> None:
    assert map_finish_reason("stop", refused=True) == "refusal"
    provider, _, http = make(ok(content=None, refusal="Tôi không thể giúp."))
    response = await provider.complete(request())
    assert response.stop_reason == "refusal"
    assert response.text == ""
    await http.aclose()


async def test_missing_usage_counts_zero(caplog: pytest.LogCaptureFixture) -> None:
    provider, _, http = make(ok(usage=False))
    with caplog.at_level("WARNING"):
        first = await provider.complete(request())
        await provider.complete(request())
    assert (first.input_tokens, first.output_tokens) == (0, 0)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert PROMPT not in warnings[0].getMessage()
    await http.aclose()


async def test_blank_response_model_uses_requested_model() -> None:
    provider, _, http = make(ok(model=""))
    assert (await provider.complete(request())).model == "gpt-test"
    await http.aclose()


def _connect_error(_req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("boom")


@pytest.mark.parametrize(
    ("handler", "error", "expected_name"),
    [
        (
            lambda _r: httpx.Response(429, json={"error": {"message": PROMPT}}),
            RetryableError,
            "RateLimitError",
        ),
        (
            lambda _r: httpx.Response(503, json={"error": {"message": SYSTEM}}),
            RetryableError,
            "InternalServerError",
        ),
        (_connect_error, RetryableError, "APIConnectionError"),
        (
            lambda _r: httpx.Response(400, json={"error": {"message": PROMPT}}),
            ExternalServiceError,
            "BadRequestError",
        ),
    ],
)
async def test_error_mapping(
    handler: Callable[[httpx.Request], httpx.Response],
    error: type[Exception],
    expected_name: str,
) -> None:
    provider, _, http = make(handler)
    with pytest.raises(error) as info:
        await provider.complete(request())
    text = str(info.value)
    assert text.startswith(IDENTITY)
    assert expected_name in text
    assert "SECRET" not in text
    await http.aclose()


async def test_aclose_closes_sdk_client() -> None:
    sdk = openai.AsyncOpenAI(base_url="http://api.example.test/v1", api_key="unused")
    provider = OpenAICompatibleProvider(
        sdk, identity=IDENTITY, structured_output="json_schema", send_reasoning_effort=False
    )
    assert not sdk.is_closed()
    await provider.aclose()
    assert sdk.is_closed()
