"""Lượt agent của provider OpenAI-compatible: chuyển đổi khối <-> Chat Completions (spec §5.3)."""

import json
import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import AgentName, AgentRunStatus, AgentTargetType
from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.llm.agent_runner import AgentRunner
from citeproof.llm.cache import InMemoryCacheStore
from citeproof.llm.calls import PgCallLogger
from citeproof.llm.providers.openai_compatible import (
    from_chat_message,
    to_chat_messages,
)
from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import CacheMode, CallContext, ToolSpec, TurnRequest
from tests.llm.test_agent_runner import (
    PARTS,
    PRICES,
    SPEC,
    LookupTool,
    budget,
    ctx,  # noqa: F401  (fixture)
    submit_tool,
)
from tests.llm.test_openai_compatible_provider import PROMPT, SYSTEM, make

TOOL_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


def tool_result(block_id: str, content: str = "r") -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": block_id, "content": content}


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def use(block_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": payload}


def convert(*messages: dict[str, Any]) -> list[dict[str, Any]]:
    return to_chat_messages("SYS", messages)[1:]


def test_system_first() -> None:
    assert to_chat_messages("SYS", [])[0] == {"role": "system", "content": "SYS"}


def test_user_string_message() -> None:
    assert convert({"role": "user", "content": "hi"}) == [{"role": "user", "content": "hi"}]


def test_user_text_blocks_joined() -> None:
    out = convert({"role": "user", "content": [text("a"), text("b")]})
    assert out == [{"role": "user", "content": "a\n\nb"}]


def test_tool_results_become_tool_messages_before_user_text() -> None:
    out = convert(
        {
            "role": "user",
            "content": [tool_result("a", "ra"), tool_result("b", "rb"), text("nudge")],
        }
    )
    assert out == [
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
        {"role": "user", "content": "nudge"},
    ]


def test_assistant_text_and_tool_use() -> None:
    out = convert(
        {
            "role": "assistant",
            "content": [
                text("thinking aloud"),
                use("c1", "lookup", {"query": "x"}),
                use("c2", "lookup", {"query": "Nguyễn"}),
            ],
        }
    )
    assert out == [
        {
            "role": "assistant",
            "content": "thinking aloud",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": json.dumps({"query": "x"})},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": json.dumps({"query": "Nguyễn"}, ensure_ascii=False),
                    },
                },
            ],
        }
    ]


def test_assistant_only_tool_use_has_null_content() -> None:
    out = convert({"role": "assistant", "content": [use("c1", "lookup", {})]})
    assert out[0]["content"] is None
    assert out[0]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_assistant_text_only_has_no_tool_calls() -> None:
    out = convert({"role": "assistant", "content": [text("done")]})
    assert out == [{"role": "assistant", "content": "done"}]


def test_thinking_and_unknown_blocks_dropped() -> None:
    out = convert(
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "s"},
                {"type": "redacted_thinking", "data": "x"},
                text("ok"),
            ],
        },
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "only"}]},
    )
    assert out == [{"role": "assistant", "content": "ok"}]


class FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = type("F", (), {"name": name, "arguments": arguments})()


def test_from_chat_message() -> None:
    blocks = from_chat_message(
        FakeMessage(
            "hello",
            [FakeCall("c1", "lookup", '{"query":"x"}'), FakeCall("c2", "submit", '{"a":1}')],
        )
    )
    assert blocks == (
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "c1", "name": "lookup", "input": {"query": "x"}},
        {"type": "tool_use", "id": "c2", "name": "submit", "input": {"a": 1}},
    )


def test_from_chat_message_null_content_has_no_text_block() -> None:
    blocks = from_chat_message(FakeMessage(None, [FakeCall("c1", "lookup", "{}")]))
    assert [b["type"] for b in blocks] == ["tool_use"]
    assert from_chat_message(FakeMessage(None)) == ()


@pytest.mark.parametrize("arguments", ["{not json", "", "[1, 2]", '"str"', "null"])
def test_invalid_arguments_json_gives_empty_input(arguments: str) -> None:
    blocks = from_chat_message(FakeMessage(None, [FakeCall("c1", "lookup", arguments)]))
    assert blocks == ({"type": "tool_use", "id": "c1", "name": "lookup", "input": {}},)


def chat_body(
    message: dict[str, Any], *, finish: str = "tool_calls", usage: bool = True
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-t",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-served",
        "choices": [
            {"index": 0, "finish_reason": finish, "message": {"role": "assistant", **message}}
        ],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 21, "completion_tokens": 5, "total_tokens": 26}
    return body


def tool_call_message(
    calls: list[tuple[str, str, dict[str, Any]]], content: str | None = None
) -> dict[str, Any]:
    return {
        "content": content,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, n, a in calls
        ],
    }


def turn_request(*, tools: bool = True, effort: str | None = "high") -> TurnRequest:
    return TurnRequest(
        model="gpt-test",
        system=SYSTEM,
        messages=({"role": "user", "content": [text(PROMPT)]},),
        tools=(ToolSpec("lookup", "Look up.", TOOL_SCHEMA),) if tools else (),
        max_tokens=99,
        effort=effort,
    )


async def test_tools_request_shape() -> None:
    provider, bodies, http = make(
        lambda _r: httpx.Response(200, json=chat_body({"content": "x"}, finish="stop"))
    )
    await provider.complete_turn(turn_request())
    body = bodies[0]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up.",
                "parameters": strict_output_schema(TOOL_SCHEMA),
            },
        }
    ]
    assert "strict" not in body["tools"][0]["function"]
    assert "tool_choice" not in body
    assert "reasoning_effort" not in body
    assert body["max_completion_tokens"] == 99
    assert body["messages"][0] == {"role": "system", "content": SYSTEM}
    assert body["messages"][1] == {"role": "user", "content": PROMPT}
    for key in ("temperature", "top_p", "top_k"):
        assert key not in body
    await http.aclose()


async def test_no_tools_omits_tools_and_effort_sent_only_when_enabled() -> None:
    provider, bodies, http = make(
        lambda _r: httpx.Response(200, json=chat_body({"content": "x"}, finish="stop")),
        send_reasoning_effort=True,
    )
    await provider.complete_turn(turn_request(tools=False))
    assert "tools" not in bodies[0]
    assert bodies[0]["reasoning_effort"] == "high"
    await http.aclose()


async def test_response_mapping() -> None:
    message = tool_call_message([("c1", "lookup", {"query": "q"})], content="plan")
    provider, _, http = make(lambda _r: httpx.Response(200, json=chat_body(message)))
    response = await provider.complete_turn(turn_request())
    assert response.content == (
        {"type": "text", "text": "plan"},
        {"type": "tool_use", "id": "c1", "name": "lookup", "input": {"query": "q"}},
    )
    assert response.stop_reason == "tool_use"
    assert (response.input_tokens, response.output_tokens) == (21, 5)
    assert response.model == "gpt-served"
    await http.aclose()


async def test_refusal_and_missing_usage(caplog: pytest.LogCaptureFixture) -> None:
    body = chat_body({"content": None, "refusal": "no"}, finish="stop", usage=False)
    provider, _, http = make(lambda _r: httpx.Response(200, json=body))
    with caplog.at_level(logging.WARNING):
        first = await provider.complete_turn(turn_request())
        await provider.complete_turn(turn_request())
    assert first.stop_reason == "refusal"
    assert first.content == ()
    assert (first.input_tokens, first.output_tokens) == (0, 0)
    assert len([r for r in caplog.records if "usage" in r.getMessage()]) == 1
    await http.aclose()


async def test_empty_choices_guard() -> None:
    body = chat_body({"content": "SECRET-BODY"})
    body["choices"] = []
    provider, _, http = make(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(ExternalServiceError, match="empty_choices") as info:
        await provider.complete_turn(turn_request())
    assert "SECRET" not in str(info.value)
    await http.aclose()


@pytest.mark.parametrize(
    ("status", "error"), [(429, RetryableError), (503, RetryableError), (400, ExternalServiceError)]
)
async def test_errors_mapped_without_leaking(status: int, error: type[Exception]) -> None:
    provider, _, http = make(lambda _r: httpx.Response(status, json={"error": "SECRET-BODY"}))
    with pytest.raises(error) as info:
        await provider.complete_turn(turn_request())
    assert f"status={status}" in str(info.value)
    assert "SECRET" not in str(info.value)
    await http.aclose()


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_agent_runner_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: CallContext,  # noqa: F811
) -> None:
    replies = iter(
        [
            chat_body(tool_call_message([("call_1", "note_lookup", {"query": "x"})])),
            chat_body(
                tool_call_message([("call_2", "submit_answer", {"answer": "yes"})]),
            ),
        ]
    )
    provider, bodies, http = make(lambda _r: httpx.Response(200, json=next(replies)))
    lookup_tool = LookupTool()
    agent_runner = AgentRunner(
        provider,
        InMemoryCacheStore(),
        PgCallLogger(session_factory),
        session_factory,
        mode=CacheMode.READ_WRITE,
        prices=PRICES,
    )
    outcome = await agent_runner.run(
        agent=AgentName.EVIDENCE_INVESTIGATOR,
        version="v1",
        tool_catalog_version="v1",
        parts=PARTS,
        tools=[lookup_tool],
        submit=submit_tool(),
        budget=budget(),
        spec=SPEC,
        ctx=ctx,
        target_type=AgentTargetType.CLAIM,
        target_id=uuid.UUID(int=7),
    )
    assert outcome.status is AgentRunStatus.SUBMITTED
    assert lookup_tool.calls == ["x"]
    assert len(bodies) == 2
    second = bodies[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["id"] == "call_1"
    assert second[3]["tool_call_id"] == "call_1"
    assert json.loads(second[3]["content"]) == {"hits": ["x"]}
    assert {t["function"]["name"] for t in bodies[0]["tools"]} == {"note_lookup", "submit_answer"}
    await http.aclose()
