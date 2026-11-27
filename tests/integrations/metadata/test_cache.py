"""MetadataCache trên PostgreSQL: khóa hash, TTL, không lưu truy vấn, không cache lỗi (T-304)."""

import hashlib
import re
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import MetadataOutcome
from citeproof.core.settings import Settings
from citeproof.db.models import MetadataCacheEntry
from citeproof.integrations.metadata import (
    METADATA_CACHE_VERSION,
    CrossrefConnector,
    MetadataCache,
    MetadataGateway,
    RateLimiter,
    WorkAuthor,
    WorkRecord,
    make_gateway_factory,
    query_hash,
)
from citeproof.llm.cache import canonical_json
from tests.integrations.metadata.fakes import FakeApi, json_response, load_fixture

PINNED_HASH = "ee492922cff2e2222b6ca8e37971212a217e25f0b04fcf3d8310da3742aef844"
SessionFactory = async_sessionmaker[AsyncSession]


async def _no_sleep(seconds: float) -> None:
    return None


def _cache(session_factory: SessionFactory, **ttl: timedelta | None) -> MetadataCache:
    return MetadataCache(
        session_factory,
        ok_ttl=ttl.get("ok_ttl", timedelta(days=30)),
        not_found_ttl=ttl.get("not_found_ttl", timedelta(days=7)),
    )


def _gateway(api: FakeApi, cache: MetadataCache) -> MetadataGateway:
    connector = CrossrefConnector(api.client(), contact_email=None)
    return MetadataGateway(
        {"crossref": connector},
        cache,
        {"crossref": RateLimiter(1000.0, sleep=_no_sleep)},
        breaker_threshold=3,
        sleep=_no_sleep,
    )


async def _rows(session_factory: SessionFactory) -> list[MetadataCacheEntry]:
    async with session_factory() as session:
        return list((await session.scalars(select(MetadataCacheEntry))).all())


def _record(title: str = "Deep Residual Learning for Image Recognition") -> WorkRecord:
    return WorkRecord(
        source="crossref",
        source_id="10.1109/cvpr.2016.90",
        title=title,
        authors=[WorkAuthor(family="He", given="Kaiming")],
        year=2016,
        doi="10.1109/cvpr.2016.90",
    )


def test_query_hash_is_sha256_of_canonical_json() -> None:
    first = query_hash({"op": "doi", "args": {"doi": "10.1000/x"}})
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert first == query_hash({"args": {"doi": "10.1000/x"}, "op": "doi"})  # thứ tự khóa không đổi
    assert first != query_hash({"op": "doi", "args": {"doi": "10.1000/y"}})


def test_query_hash_is_versioned_and_pinned() -> None:
    assert METADATA_CACHE_VERSION == 2
    payload = {"op": "doi", "args": {"doi": "10.1000/x"}}
    assert query_hash(payload) == PINNED_HASH
    old = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    assert query_hash(payload) != old  # khóa cũ (chưa có phiên bản) không còn trúng
    assert query_hash(payload) == query_hash(dict(payload))


async def test_cache_hit_skips_http(session_factory: SessionFactory, clean_db: None) -> None:
    api = FakeApi(lambda request: json_response(load_fixture("crossref_doi_he")))
    cache = _cache(session_factory)

    first = await _gateway(api, cache).by_doi("crossref", "10.1109/CVPR.2016.90")
    # Gateway mới (lần chạy sau) vẫn đọc được cache dùng chung; DOI viết hoa khác vẫn trúng.
    second = await _gateway(api, cache).by_doi("crossref", "https://doi.org/10.1109/cvpr.2016.90")

    assert api.calls == 1
    assert first.status == second.status == "ok"
    assert second.records == first.records
    assert second.records[0].title == "Deep Residual Learning for Image Recognition"


async def test_cache_stores_hash_not_query(session_factory: SessionFactory, clean_db: None) -> None:
    # Truy vấn sinh từ bài của người dùng (ở đây cố ý gõ sai) không được nằm trong cache.
    typo_title = "Atention is al you nead for vietnamse thesis"
    typo_author = "Vaswanni, Ashsih"
    api = FakeApi(lambda request: json_response({"status": "ok", "message": {"items": []}}))
    ref = ParsedReference(title=typo_title, authors=[typo_author], year=2017)

    outcome = await _gateway(api, _cache(session_factory)).search("crossref", ref)

    assert outcome.status == "not_found"
    (row,) = await _rows(session_factory)
    assert re.fullmatch(r"[0-9a-f]{64}", row.query_hash)
    assert row.outcome == MetadataOutcome.NOT_FOUND.value
    assert row.response == {"records": []}
    for text in (typo_title, typo_author, "Atention", "Vaswanni"):
        assert text not in str(row.response)
        assert text not in row.query_hash


async def test_ok_response_is_records_only(session_factory: SessionFactory, clean_db: None) -> None:
    cache = _cache(session_factory)
    payload = {"op": "search_text", "args": {"query": "residual learning", "year": None}}
    await cache.put("crossref", payload, MetadataOutcome.OK, [_record()])

    (row,) = await _rows(session_factory)
    assert set(row.response) == {"records"}
    assert "residual learning" not in str(row.response)  # truy vấn khác tiêu đề đã tìm thấy
    assert row.query_hash == query_hash(payload)
    hit = await cache.get("crossref", payload)
    assert hit is not None
    assert hit.outcome is MetadataOutcome.OK
    assert hit.records == [_record()]


async def test_not_found_ttl_seven_days(session_factory: SessionFactory, clean_db: None) -> None:
    cache = _cache(session_factory)
    await cache.put(
        "openalex", {"op": "doi", "args": {"doi": "10.1000/a"}}, MetadataOutcome.NOT_FOUND, []
    )
    await cache.put(
        "openalex", {"op": "doi", "args": {"doi": "10.1000/b"}}, MetadataOutcome.OK, [_record()]
    )

    ttl = {row.outcome: row.expires_at - row.fetched_at for row in await _rows(session_factory)}
    assert ttl == {"not_found": timedelta(days=7), "ok": timedelta(days=30)}


async def test_expired_rows_are_ignored(session_factory: SessionFactory, clean_db: None) -> None:
    api = FakeApi(lambda request: json_response(load_fixture("crossref_doi_he")))
    cache = _cache(session_factory)
    await _gateway(api, cache).by_doi("crossref", "10.1109/cvpr.2016.90")
    async with session_factory() as session:
        await session.execute(
            update(MetadataCacheEntry).values(
                expires_at=MetadataCacheEntry.fetched_at - timedelta(seconds=1)
            )
        )
        await session.commit()

    assert (
        await cache.get("crossref", {"op": "doi", "args": {"doi": "10.1109/cvpr.2016.90"}}) is None
    )
    outcome = await _gateway(api, cache).by_doi("crossref", "10.1109/cvpr.2016.90")
    assert outcome.status == "ok"
    assert api.calls == 2
    (row,) = await _rows(session_factory)  # ghi đè dòng cũ (upsert), không thêm dòng
    assert row.expires_at is not None
    assert row.expires_at - row.fetched_at == timedelta(days=30)


async def test_put_upserts_outcome(session_factory: SessionFactory, clean_db: None) -> None:
    cache = _cache(session_factory)
    payload: dict[str, Any] = {"op": "doi", "args": {"doi": "10.1000/x"}}
    await cache.put("crossref", payload, MetadataOutcome.NOT_FOUND, [])
    await cache.put("crossref", payload, MetadataOutcome.OK, [_record()])

    (row,) = await _rows(session_factory)
    assert row.outcome == "ok"
    hit = await cache.get("crossref", payload)
    assert hit is not None
    assert [r.title for r in hit.records] == [_record().title]
    assert await cache.get("openalex", payload) is None  # khóa gồm cả nguồn


async def test_errors_are_never_cached(session_factory: SessionFactory, clean_db: None) -> None:
    cache = _cache(session_factory)
    await cache.put(
        "crossref", {"op": "doi", "args": {"doi": "10.1000/x"}}, MetadataOutcome.ERROR, []
    )
    api = FakeApi(lambda request: httpx.Response(500))
    outcome = await _gateway(api, cache).by_doi("crossref", "10.1000/y")

    assert outcome.status == "error"
    assert await _rows(session_factory) == []


async def test_unreadable_cached_response_is_a_miss(
    session_factory: SessionFactory, clean_db: None
) -> None:
    cache = _cache(session_factory)
    payload = {"op": "doi", "args": {"doi": "10.1000/x"}}
    await cache.put("crossref", payload, MetadataOutcome.OK, [_record()])
    async with session_factory() as session:
        await session.execute(
            update(MetadataCacheEntry).values(response={"records": [{"title": None}]})
        )
        await session.commit()
    assert await cache.get("crossref", payload) is None


async def test_no_ttl_rows_never_expire(session_factory: SessionFactory, clean_db: None) -> None:
    cache = _cache(session_factory, ok_ttl=None, not_found_ttl=None)
    payload = {"op": "doi", "args": {"doi": "10.1000/x"}}
    await cache.put("crossref", payload, MetadataOutcome.NOT_FOUND, [])

    (row,) = await _rows(session_factory)
    assert row.expires_at is None
    hit = await cache.get("crossref", payload)
    assert hit is not None
    assert hit.outcome is MetadataOutcome.NOT_FOUND


async def test_factory_ttl_from_settings_and_eval_switch(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        metadata_cache_ttl_days=3,
        metadata_not_found_ttl_days=1,
    )
    api = FakeApi(lambda request: json_response(load_fixture("crossref_doi_he")))
    http = api.client()
    product = make_gateway_factory(settings, http, session_factory, sleep=_no_sleep)()
    await product.by_doi("crossref", "10.1109/cvpr.2016.90")
    (row,) = await _rows(session_factory)
    assert row.expires_at is not None
    assert row.expires_at - row.fetched_at == timedelta(days=3)

    # Bộ đánh giá (preflight F17): không TTL trong DB đánh giá.
    api_404 = FakeApi(lambda request: httpx.Response(404))
    evaluation = make_gateway_factory(
        settings, api_404.client(), session_factory, ttl_enabled=False, sleep=_no_sleep
    )()
    assert (await evaluation.by_doi("crossref", "10.1000/none")).status == "not_found"
    rows = {row.outcome: row for row in await _rows(session_factory)}
    assert rows["not_found"].expires_at is None
