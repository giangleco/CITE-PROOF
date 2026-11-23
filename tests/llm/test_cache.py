import unicodedata
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models.llm import LLMCacheEntry
from citeproof.llm.cache import (
    CacheEntryMeta,
    InMemoryCacheStore,
    PgCacheStore,
    agent_step_cache_key,
    canonical_json,
    doc_namespace,
    eval_namespace,
    worker_cache_key,
)
from tests.db.factories import make_document

BASE: dict[str, Any] = {
    "provider": "p",
    "model": "m",
    "task": "t",
    "prompt_version": "v1",
    "schema_version": "s1",
}


def test_nfc_and_nfd_inputs_share_cache_key() -> None:
    nfc = unicodedata.normalize("NFC", "Nguyễn")
    nfd = unicodedata.normalize("NFD", "Nguyễn")
    assert nfc != nfd
    assert worker_cache_key(**BASE, inputs={"a": nfc}, attempt=1) == worker_cache_key(
        **BASE, inputs={"a": nfd}, attempt=1
    )


def test_key_ignores_dict_order_but_tracks_versions_and_attempt() -> None:
    k = worker_cache_key(**BASE, inputs={"a": 1, "b": 2}, attempt=1)
    assert k == worker_cache_key(**BASE, inputs={"b": 2, "a": 1}, attempt=1)
    assert k != worker_cache_key(
        **{**BASE, "prompt_version": "v2"}, inputs={"a": 1, "b": 2}, attempt=1
    )
    assert k != worker_cache_key(
        **{**BASE, "schema_version": "s2"}, inputs={"a": 1, "b": 2}, attempt=1
    )
    assert k != worker_cache_key(**BASE, inputs={"a": 1, "b": 2}, attempt=2)


def test_agent_step_key_tracks_messages() -> None:
    kw: dict[str, Any] = {
        "agent": "a",
        "agent_version": "1",
        "tool_catalog_version": "c1",
        "model": "m",
    }
    assert agent_step_cache_key(**kw, messages=[{"r": "u"}]) != agent_step_cache_key(
        **kw, messages=[{"r": "x"}]
    )


def test_namespaces() -> None:
    d = UUID(int=1)
    assert doc_namespace(d) == f"doc:{d}"
    assert eval_namespace("D2", "ab12") == "eval:D2@ab12"


class _M(BaseModel):
    x: int


def test_canonical_json_types() -> None:
    d = UUID(int=1)
    out = canonical_json(
        {"b": (1, 2), "a": [d, _M(x=1), datetime(2026, 1, 1, tzinfo=UTC), None, True]}
    )
    assert out == (
        '{"a":["00000000-0000-0000-0000-000000000001",{"x":1},"2026-01-01T00:00:00+00:00",null,true],'
        '"b":[1,2]}'
    )
    assert canonical_json("é") == '"é"'


@pytest.mark.parametrize("bad", [{1}, b"x", object(), {1: "a"}])
def test_canonical_json_rejects_unsupported(bad: Any) -> None:
    with pytest.raises(TypeError):
        canonical_json(bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), datetime(2026, 1, 1)])
def test_canonical_json_rejects_values(bad: Any) -> None:
    with pytest.raises(ValueError):
        canonical_json(bad)


def test_canonical_json_rejects_key_collision_after_nfc() -> None:
    nfc = unicodedata.normalize("NFC", "é")
    nfd = unicodedata.normalize("NFD", "é")
    with pytest.raises(ValueError):
        canonical_json({nfc: 1, nfd: 2})


async def test_doc_namespace_without_document_rejected() -> None:
    meta = CacheEntryMeta("doc:x", None, "t", "m", "v1", "s1")
    with pytest.raises(ValueError):
        await InMemoryCacheStore().put("k", meta, {})


async def test_in_memory_roundtrip_first_put_wins() -> None:
    store = InMemoryCacheStore()
    meta = CacheEntryMeta("eval:D@h", None, "t", "m", "v1", "s1")
    assert await store.get("k") is None
    await store.put("k", meta, {"a": 1})
    await store.put("k", meta, {"a": 2})
    assert await store.get("k") == {"a": 1}


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_pg_cache_roundtrip_counts_hits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PgCacheStore(session_factory)
    meta = CacheEntryMeta("eval:D2@ab12", None, "t", "m", "v1", "s1")
    assert await store.get("k") is None
    await store.put("k", meta, {"a": 1})
    await store.put("k", meta, {"a": 2})
    assert await store.get("k") == {"a": 1}
    assert await store.get("k") == {"a": 1}
    async with session_factory() as session:
        entry = (await session.execute(select(LLMCacheEntry))).scalar_one()
        assert entry.hit_count == 2


@pytest.mark.db
@pytest.mark.usefixtures("clean_db")
async def test_pg_doc_namespace_cascades_on_document_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        doc = await make_document(session)
        await session.commit()
        doc_id = doc.id
    store = PgCacheStore(session_factory)
    meta = CacheEntryMeta(doc_namespace(doc_id), doc_id, "t", "m", "v1", "s1")
    await store.put("k", meta, {"a": 1})
    with pytest.raises(ValueError):
        await store.put("k2", CacheEntryMeta("doc:x", None, "t", "m", "v1", "s1"), {})
    async with session_factory() as session:
        assert (await session.execute(select(LLMCacheEntry))).scalar_one().document_id == doc_id
        await session.delete(await session.get(type(doc), doc_id))
        await session.commit()
    async with session_factory() as session:
        assert (await session.execute(select(LLMCacheEntry))).first() is None
