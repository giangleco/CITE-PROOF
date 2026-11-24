import json
from decimal import Decimal
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from citeproof.core.errors import LLMOutputError, RetryableError
from citeproof.core.logging import configure_logging
from citeproof.llm.cache import (
    CacheEntryMeta,
    InMemoryCacheStore,
    doc_namespace,
    eval_namespace,
    worker_cache_key,
)
from citeproof.llm.calls import InMemoryCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.context import PromptParts
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import (
    CacheEntryInvalidError,
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
    call_logger = InMemoryCallLogger()
    client = LLMClient(provider, store, call_logger=call_logger, mode=mode, prices=prices)
    return client, provider, store, call_logger


async def call(client: LLMClient, ctx: CallContext = CTX) -> str:
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=ctx)
    return result.value.answer


async def test_miss_calls_provider_then_hit_skips_it() -> None:
    client, provider, _, call_logger = make([GOOD])
    assert await call(client) == "ok"
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert result.value.answer == "ok"
    assert result.cache_hit
    assert len(provider.requests) == 1
    assert [r.cache_hit for r in call_logger.records] == [False, True]
    assert call_logger.records[1].input_tokens == 0
    assert call_logger.records[1].cost_estimate == 0


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


async def test_feedback_lists_loc_keys_but_never_input_values() -> None:
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
    client, _, _, call_logger = make([bad, good], prices={"m": (3.0, 15.0)})
    await call(client)
    assert [r.cost_estimate for r in call_logger.records] == [Decimal(3), Decimal(15)]
    assert all(not r.cache_hit and r.prompt_version == "v1" for r in call_logger.records)


async def test_doc_namespace_cache_entry_carries_document_id() -> None:
    client, _, store, call_logger = make([GOOD])
    await call(client)
    assert len(store._entries) == 1
    assert call_logger.records[0].cache_namespace == f"doc:{DOC}"


async def test_eval_namespace_is_accepted() -> None:
    ctx = CallContext(eval_namespace("D2", "abc"), None, None, None)
    client, _, store, _ = make([GOOD])
    assert await call(client, ctx) == "ok"
    assert len(store._entries) == 1


async def test_invalid_namespace_fails_before_provider_call() -> None:
    ctx = CallContext("bogus", None, None, None)
    client, provider, _, call_logger = make([GOOD])
    with pytest.raises(ValueError):
        await call(client, ctx)
    assert provider.requests == [] and call_logger.records == []


async def test_provider_error_propagates_unlogged() -> None:
    client, _, store, call_logger = make([RetryableError("429")])
    with pytest.raises(RetryableError):
        await call(client)
    assert call_logger.records == [] and store._entries == {}


async def test_replay_only_happy_path_after_read_write() -> None:
    client, _, store, _ = make([GOOD])
    await call(client)
    replay, provider, _, call_logger = make([], CacheMode.REPLAY_ONLY, store)
    assert await call(replay) == "ok"
    assert provider.requests == [] and call_logger.records[0].cache_hit


async def test_retried_call_is_replayable_under_one_key() -> None:
    bad = ProviderResponse('{"bad":1}', 1, 1, "end_turn")
    client, _, store, call_logger = make([bad, GOOD])
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert list(store._entries) == [result.cache_key]
    assert {r.cache_key for r in call_logger.records} == {result.cache_key}
    replay, provider, _, _ = make([], CacheMode.REPLAY_ONLY, store)
    assert await call(replay) == "ok"
    assert provider.requests == []
    again, provider2, _, _ = make([], CacheMode.READ_WRITE, store)
    assert await call(again) == "ok"
    assert provider2.requests == []


async def test_max_attempts_one_with_invalid_output_raises() -> None:
    client, _, _, _ = make([ProviderResponse("{}", 1, 1, "end_turn")])
    with pytest.raises(LLMOutputError):
        await client.complete_structured(
            parts=PARTS, schema=Echo, model="m", ctx=CTX, max_attempts=1
        )


@pytest.mark.parametrize("mode", [CacheMode.FRESH, CacheMode.REPLAY_ONLY])
async def test_invalid_namespace_raises_in_every_mode(mode: CacheMode) -> None:
    client, provider, _, _ = make([GOOD], mode)
    with pytest.raises(ValueError):
        await call(client, CallContext("bogus", None, None, None))
    assert provider.requests == []


@pytest.mark.parametrize("entry", [{}, {"value": {"wrong": 1}}])
async def test_corrupt_entry_is_regenerated_in_read_write(entry: dict[str, object]) -> None:
    client, _, store, _ = make([GOOD])
    await call(client)
    (key,) = store._entries
    store._entries[key] = entry
    client2, provider, _, _ = make([GOOD], CacheMode.READ_WRITE, store)
    assert await call(client2) == "ok"
    assert len(provider.requests) == 1
    assert store._entries[key] == {"value": {"answer": "ok"}, "model": "m"}


@pytest.mark.parametrize("entry", [{}, {"value": {"wrong": 1}}])
async def test_corrupt_entry_raises_in_replay_only(entry: dict[str, object]) -> None:
    client, _, store, _ = make([GOOD])
    await call(client)
    (key,) = store._entries
    store._entries[key] = entry
    replay, provider, _, _ = make([], CacheMode.REPLAY_ONLY, store)
    with pytest.raises(CacheEntryInvalidError) as info:
        await call(replay)
    assert info.value.cache_key == key
    assert provider.requests == []


async def test_invalid_entry_warning_carries_cache_key_but_no_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _, store, _ = make([GOOD])
    await call(client)
    (key,) = store._entries
    store._entries[key] = {"value": {"wrong": "bí mật"}}
    configure_logging("INFO")
    client2, _, _, _ = make([GOOD], CacheMode.READ_WRITE, store)
    await call(client2)
    err = capsys.readouterr().err
    (line,) = [ln for ln in err.splitlines() if "llm_cache_entry_invalid" in ln]
    rec = json.loads(line)
    assert rec["cache_key"] == key
    assert rec["task"] == "echo"
    assert "bí mật" not in err
    assert "wrong" not in err


class _RacingStore(InMemoryCacheStore):
    """Worker khác ghi cùng khoá giữa lúc ta đọc miss và lúc ta ghi."""

    async def put(self, key: str, meta: CacheEntryMeta, response: dict[str, Any]) -> dict[str, Any]:
        await super().put(key, meta, {"value": {"answer": "first"}})
        return await super().put(key, meta, response)


async def test_concurrent_writer_entry_wins_so_live_equals_replay() -> None:
    store = _RacingStore()
    client, provider, _, _ = make([GOOD], cache=store)
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert result.value.answer == "first"
    assert not result.cache_hit
    assert len(provider.requests) == 1
    replay, _, _, _ = make([], CacheMode.REPLAY_ONLY, store)
    assert await call(replay) == "first"


def _legacy_key() -> str:
    """Khoá như trước khi có `effort`: inputs chỉ gồm system và messages."""
    return worker_cache_key(
        namespace=CTX.namespace,
        provider="scripted",
        model="m",
        task="echo",
        prompt_version="v1",
        schema_version="echo/1",
        inputs={"system": "sys", "messages": [{"role": "user", "content": "hello"}]},
        attempt=1,
    )


async def test_no_effort_keeps_existing_key() -> None:
    client, provider, store, _ = make([GOOD])
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert result.cache_key == _legacy_key()
    assert list(store._entries) == [_legacy_key()]
    assert provider.requests[0].effort is None


async def test_effort_changes_cache_key() -> None:
    client, provider, store, _ = make([GOOD, GOOD, GOOD])
    keys = []
    for effort in (None, "low", "high"):
        result = await client.complete_structured(
            parts=PARTS, schema=Echo, model="m", ctx=CTX, effort=effort
        )
        assert not result.cache_hit
        keys.append(result.cache_key)
    assert len(set(keys)) == 3
    assert [r.effort for r in provider.requests] == [None, "low", "high"]
    again, provider2, _, _ = make([], CacheMode.REPLAY_ONLY, store)
    hit = await again.complete_structured(
        parts=PARTS, schema=Echo, model="m", ctx=CTX, effort="low"
    )
    assert hit.cache_hit and hit.cache_key == keys[1]
    assert provider2.requests == []


async def test_effort_is_kept_on_schema_retry() -> None:
    bad = ProviderResponse('{"bad":1}', 1, 1, "end_turn")
    client, provider, _, _ = make([bad, GOOD])
    await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX, effort="low")
    assert [r.effort for r in provider.requests] == ["low", "low"]


async def test_served_model_recorded() -> None:
    served = ProviderResponse('{"answer":"ok"}', 10, 5, "end_turn", model="claude-sonnet-5")
    client, _, store, call_logger = make([served])
    result = await client.complete_structured(
        parts=PARTS, schema=Echo, model="claude-sonnet-5-5", ctx=CTX
    )
    assert result.model == "claude-sonnet-5"
    assert call_logger.records[0].model == "claude-sonnet-5"
    assert store._entries[result.cache_key] == {
        "value": {"answer": "ok"},
        "model": "claude-sonnet-5",
    }
    hit = await client.complete_structured(
        parts=PARTS, schema=Echo, model="claude-sonnet-5-5", ctx=CTX
    )
    assert hit.cache_hit
    assert hit.model == "claude-sonnet-5"
    assert call_logger.records[1].model == "claude-sonnet-5"


async def test_empty_served_model_means_requested() -> None:
    client, _, store, call_logger = make([GOOD])
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert result.model == "m"
    assert call_logger.records[0].model == "m"
    assert store._entries[result.cache_key]["model"] == "m"


async def test_legacy_entry_without_model_reports_requested_model() -> None:
    store = InMemoryCacheStore()
    store._entries[_legacy_key()] = {"value": {"answer": "old"}}
    client, provider, _, call_logger = make([], CacheMode.REPLAY_ONLY, store)
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert (result.value.answer, result.model) == ("old", "m")
    assert call_logger.records[0].model == "m"
    assert provider.requests == []


async def test_each_attempt_logs_its_own_served_model() -> None:
    bad = ProviderResponse('{"bad":1}', 1, 1, "end_turn", model="claude-sonnet-5-5")
    good = ProviderResponse('{"answer":"ok"}', 1, 1, "end_turn", model="claude-sonnet-5")
    client, _, _, call_logger = make([bad, good])
    result = await client.complete_structured(
        parts=PARTS, schema=Echo, model="claude-sonnet-5-5", ctx=CTX
    )
    assert [r.model for r in call_logger.records] == ["claude-sonnet-5-5", "claude-sonnet-5"]
    assert result.model == "claude-sonnet-5"


async def test_cost_uses_served_model_price_then_requested() -> None:
    served = ProviderResponse('{"answer":"ok"}', 1_000_000, 0, "end_turn", model="fallback-m")
    client, _, _, call_logger = make([served], prices={"fallback-m": (2.0, 10.0), "m": (9.0, 9.0)})
    await call(client)
    assert call_logger.records[0].cost_estimate == Decimal(2)

    unpriced = ProviderResponse('{"answer":"ok"}', 1_000_000, 0, "end_turn", model="unpriced-m")
    client, _, _, call_logger = make([unpriced], prices={"m": (3.0, 15.0)})
    await call(client)
    assert call_logger.records[0].model == "unpriced-m"
    assert call_logger.records[0].cost_estimate == Decimal(3)


async def test_concurrent_writer_model_wins_too() -> None:
    class _Racing(InMemoryCacheStore):
        async def put(
            self, key: str, meta: CacheEntryMeta, response: dict[str, Any]
        ) -> dict[str, Any]:
            await super().put(key, meta, {"value": {"answer": "first"}, "model": "other-m"})
            return await super().put(key, meta, response)

    served = ProviderResponse('{"answer":"ok"}', 1, 1, "end_turn", model="served-m")
    client, _, _, _ = make([served], cache=_Racing())
    result = await client.complete_structured(parts=PARTS, schema=Echo, model="m", ctx=CTX)
    assert (result.value.answer, result.model) == ("first", "other-m")


@pytest.mark.parametrize("text", ["", "  \n"])
async def test_empty_output_is_not_echoed_as_assistant_turn(text: str) -> None:
    # API từ chối lượt assistant rỗng: lần thử lại chỉ thêm phản hồi lỗi của user.
    refused = ProviderResponse(text, 1, 0, "refusal")
    client, provider, _, _ = make([refused, GOOD])
    assert await call(client) == "ok"
    retry = provider.requests[1].messages
    assert [m["role"] for m in retry] == ["user", "user"]
    assert "stop_reason=refusal" in retry[-1]["content"]


class _ClosingProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


async def test_aclose_closes_provider() -> None:
    provider = _ClosingProvider()
    client = LLMClient(provider, InMemoryCacheStore(), InMemoryCallLogger())
    await client.aclose()
    assert provider.closed == 1


async def test_aclose_without_provider_close_is_noop() -> None:
    client = LLMClient(ScriptedProvider([]), InMemoryCacheStore(), InMemoryCallLogger())
    await client.aclose()
