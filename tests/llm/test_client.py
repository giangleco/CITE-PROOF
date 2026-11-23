from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

import pytest

from citeproof.core.errors import LLMOutputError, RetryableError
from citeproof.llm.cache import InMemoryCacheStore, doc_namespace, eval_namespace
from citeproof.llm.calls import InMemoryCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.context import PromptParts
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import (
    CacheMissError,
    CacheMode,
    CallContext,
    OutputModel,
    ProviderResponse,
)


class Echo(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "echo/1"
    answer: str


PARTS = PromptParts(task="echo", prompt_version="v1", system="sys", user="hello")
DOC = uuid4()
CTX = CallContext(namespace=doc_namespace(DOC), document_id=DOC, run_id=None, stage="s")
GOOD = ProviderResponse('{"answer":"ok"}', 10, 5, "end_turn")


def make(
    responses: list[ProviderResponse | BaseException],
    mode: CacheMode = CacheMode.READ_WRITE,
    cache: InMemoryCacheStore | None = None,
    prices: dict[str, tuple[float, float]] | None = None,
) -> tuple[LLMClient, ScriptedProvider, InMemoryCacheStore, InMemoryCallLogger]:
    provider = ScriptedProvider(responses)
    store = cache or InMemoryCacheStore()
    logger = InMemoryCallLogger()
    return LLMClient(provider, store, logger, mode=mode, prices=prices), provider, store, logger


async def call(client: LLMClient, ctx: CallContext = CTX) -> str:
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=ctx)
    return result.value.answer


async def test_miss_calls_provider_then_hit_skips_it() -> None:
    client, provider, _, logger = make([GOOD])
    assert await call(client) == "ok"
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert result.value.answer == "ok"
    assert result.cache_hit
    assert len(provider.requests) == 1
    assert [r.cache_hit for r in logger.records] == [False, True]
    assert logger.records[1].input_tokens == 0
    assert logger.records[1].cost_estimate == 0


async def test_replay_only_raises_on_miss() -> None:
    client, provider, _, _ = make([GOOD], CacheMode.REPLAY_ONLY)
    with pytest.raises(CacheMissError) as info:
        await call(client)
    assert info.value.cache_key
    assert provider.requests == []


async def test_fresh_mode_neither_reads_nor_writes() -> None:
    seed, _, store, _ = make([GOOD])
    await call(seed)
    before = dict(store._entries)
    client, provider, _, _ = make(
        [ProviderResponse('{"answer":"new"}', 1, 1, "end_turn")], CacheMode.FRESH, store
    )
    assert await call(client) == "new"
    assert len(provider.requests) == 1
    assert store._entries == before


async def test_schema_error_retries_with_feedback() -> None:
    bad = ProviderResponse('{"bad":1}', 1, 1, "end_turn")
    client, provider, _, _ = make([bad, GOOD])
    assert await call(client) == "ok"
    msgs = provider.requests[1].messages
    assert msgs[1]["role"] == "assistant"
    assert msgs[-1]["content"].startswith("SCHEMA_INVALID:")


async def test_feedback_does_not_echo_input_values() -> None:
    bad = ProviderResponse('{"answer":"ok","bí mật":"bí mật"}', 1, 1, "end_turn")
    client, provider, _, _ = make([bad, GOOD])
    await call(client)
    feedback = provider.requests[1].messages[-1]["content"]
    assert "bí mật" in feedback  # tên trường (loc) được phép
    bad2 = ProviderResponse('{"answer":123456789}', 1, 1, "end_turn")
    client, provider, _, _ = make([bad2, GOOD])
    await call(client)
    assert "123456789" not in provider.requests[1].messages[-1]["content"]


async def test_two_invalid_outputs_raise_llm_output_error() -> None:
    bad = ProviderResponse("not json", 1, 1, "end_turn")
    client, _, store, _ = make([bad, bad])
    with pytest.raises(LLMOutputError):
        await call(client)
    assert store._entries == {}


async def test_max_tokens_stop_reason_is_invalid() -> None:
    cut = ProviderResponse('{"answer":"ok"}', 1, 1, "max_tokens")
    client, provider, _, _ = make([cut, GOOD])
    assert await call(client) == "ok"
    assert "stop_reason=max_tokens" in provider.requests[1].messages[-1]["content"]


async def test_every_provider_attempt_is_logged_with_cost() -> None:
    bad = ProviderResponse("{}", 1_000_000, 0, "end_turn")
    good = ProviderResponse('{"answer":"ok"}', 0, 1_000_000, "end_turn")
    client, _, _, logger = make([bad, good], prices={"m": (3.0, 15.0)})
    await call(client)
    assert [r.cost_estimate for r in logger.records] == [Decimal(3), Decimal(15)]
    assert all(not r.cache_hit and r.prompt_version == "v1" for r in logger.records)


async def test_doc_namespace_cache_entry_carries_document_id() -> None:
    client, _, store, logger = make([GOOD])
    await call(client)
    assert len(store._entries) == 1
    assert logger.records[0].cache_namespace == f"doc:{DOC}"


async def test_eval_namespace_is_accepted() -> None:
    ctx = CallContext(eval_namespace("D2", "abc"), None, None, None)
    client, _, store, _ = make([GOOD])
    assert await call(client, ctx) == "ok"
    assert len(store._entries) == 1


async def test_invalid_namespace_fails_before_provider_call() -> None:
    ctx = CallContext("bogus", None, None, None)
    client, provider, _, logger = make([GOOD])
    with pytest.raises(ValueError):
        await call(client, ctx)
    assert provider.requests == [] and logger.records == []


async def test_provider_error_propagates_unlogged() -> None:
    client, _, store, logger = make([RetryableError("429")])
    with pytest.raises(RetryableError):
        await call(client)
    assert logger.records == [] and store._entries == {}
