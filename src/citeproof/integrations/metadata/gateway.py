"""Cổng tra cứu metadata mà S3 và A1 dùng (spec §7.3, `07a` §4.3.5, T-304).

Mỗi lời gọi: kiểm tra ngắt mạch → cache → giới hạn tốc độ → connector, thử lại tối đa 2
lần khi `RetryableError` (chờ `min(Retry-After, 10)` giây, không có thì 1 s rồi 2 s) → ghi
cache. Lỗi của nguồn trở thành kết quả `error` (không ném ra) để S3 ghi nguồn đó vào
`sources_failed`; lỗi khác (lỗi lập trình, DB) vẫn lan ra ngoài.

`make_gateway_factory` dựng connector, cache và limiter một lần cho mỗi tiến trình; mỗi
lần gọi factory trả một gateway mới với bộ ngắt mạch mới (ngắt mạch theo lần chạy).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core import defaults
from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.core.enums import MetadataOutcome
from citeproof.core.errors import ExternalServiceError, InternalError, RetryableError
from citeproof.core.settings import Settings
from citeproof.integrations.metadata.base import SOURCES, MetadataConnector, search_fields
from citeproof.integrations.metadata.breaker import CircuitBreaker
from citeproof.integrations.metadata.cache import LookupCache, MetadataCache
from citeproof.integrations.metadata.crossref import CrossrefConnector
from citeproof.integrations.metadata.models import WorkRecord
from citeproof.integrations.metadata.openalex import OpenAlexConnector
from citeproof.integrations.metadata.ratelimit import RateLimiter
from citeproof.integrations.metadata.semantic_scholar import SemanticScholarConnector

logger = logging.getLogger(__name__)

LookupStatus = Literal["ok", "not_found", "error", "circuit_open"]
Sleep = Callable[[float], Awaitable[None]]
MAX_RETRIES = 2
MAX_RETRY_AFTER_S = 10.0
BACKOFF_S = (1.0, 2.0)

_STATUS: Mapping[MetadataOutcome, LookupStatus] = {
    MetadataOutcome.OK: "ok",
    MetadataOutcome.NOT_FOUND: "not_found",
}


@dataclass(frozen=True)
class LookupOutcome:
    source: str
    status: LookupStatus
    records: list[WorkRecord]


Call = Callable[[MetadataConnector], Awaitable[list[WorkRecord]]]


class MetadataGateway:
    def __init__(
        self,
        connectors: Mapping[str, MetadataConnector],
        cache: LookupCache,
        limiters: Mapping[str, RateLimiter],
        *,
        breaker_threshold: int,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._connectors = dict(connectors)
        self._cache = cache
        self._limiters = limiters
        self._breakers = {name: CircuitBreaker(breaker_threshold) for name in self._connectors}
        self._sleep = sleep

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(self._connectors)

    @property
    def limiters(self) -> Mapping[str, RateLimiter]:
        return self._limiters

    @property
    def cache(self) -> LookupCache:
        return self._cache

    def available(self, source: str) -> bool:
        """Nguồn có trong gateway và chưa bị ngắt mạch trong lần chạy này."""
        breaker = self._breakers.get(source)
        return breaker is not None and not breaker.is_open

    async def by_doi(self, source: str, doi: str) -> LookupOutcome:
        """Tra theo DOI; DOI không hợp lệ → `not_found` mà không gọi API."""
        self._connector(source)
        normalized = normalize_doi(doi)
        if normalized is None:
            return LookupOutcome(source, "not_found", [])

        async def call(connector: MetadataConnector) -> list[WorkRecord]:
            record = await connector.get_by_doi(normalized)
            return [] if record is None else [record]

        return await self._lookup(source, "doi", {"doi": normalized}, call)

    async def search(self, source: str, ref: ParsedReference, limit: int = 5) -> LookupOutcome:
        """Tìm theo trường của tài liệu tham khảo (khóa cache chỉ gồm `search_fields`)."""

        async def call(connector: MetadataConnector) -> list[WorkRecord]:
            return await connector.search(ref, limit)

        return await self._lookup(source, "search", {**search_fields(ref), "limit": limit}, call)

    async def search_text(
        self, source: str, query: str, *, year: int | None = None, limit: int = 5
    ) -> LookupOutcome:
        """Tìm theo một chuỗi tự do (công cụ `meta_search` của A1), lọc năm ±1 khi có `year`."""

        async def call(connector: MetadataConnector) -> list[WorkRecord]:
            return await connector.search_text(query, year=year, limit=limit)

        args = {"query": query, "year": year, "limit": limit}
        return await self._lookup(source, "search_text", args, call)

    def _connector(self, source: str) -> MetadataConnector:
        connector = self._connectors.get(source)
        if connector is None:
            raise InternalError(f"Nguồn metadata không được hỗ trợ: {source!r}")
        return connector

    async def _lookup(
        self, source: str, op: str, args: Mapping[str, Any], call: Call
    ) -> LookupOutcome:
        connector = self._connector(source)
        breaker = self._breakers[source]
        if breaker.is_open:
            return LookupOutcome(source, "circuit_open", [])
        payload = {"op": op, "args": dict(args)}
        cached = await self._cache.get(source, payload)
        if cached is not None and cached.outcome in _STATUS:
            return LookupOutcome(source, _STATUS[cached.outcome], cached.records)
        try:
            records = await self._call_with_retries(source, connector, call)
        except ExternalServiceError as exc:
            breaker.record_failure()
            # Chỉ nguồn và mã lỗi; không bao giờ ghi truy vấn hay URL.
            logger.warning(
                "metadata_lookup_failed source=%s code=%s",
                source,
                exc.code,
                extra={"error_type": type(exc).__name__},
            )
            return LookupOutcome(source, "error", [])
        breaker.record_success()
        outcome = MetadataOutcome.OK if records else MetadataOutcome.NOT_FOUND
        await self._cache.put(source, payload, outcome, records)
        return LookupOutcome(source, _STATUS[outcome], records)

    async def _call_with_retries(
        self, source: str, connector: MetadataConnector, call: Call
    ) -> list[WorkRecord]:
        limiter = self._limiters.get(source)
        attempt = 0
        while True:
            if limiter is not None:
                await limiter.acquire()
            try:
                return await call(connector)
            except RetryableError as exc:
                if attempt >= MAX_RETRIES:
                    raise
                delay = retry_delay(exc, attempt)
            attempt += 1
            await self._sleep(delay)


def retry_delay(error: RetryableError, attempt: int) -> float:
    """`min(Retry-After, 10)` khi nguồn gửi kèm, ngược lại 1 s rồi 2 s."""
    if error.retry_after is not None:
        return min(error.retry_after, MAX_RETRY_AFTER_S)
    return BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]


def make_gateway_factory(
    settings: Settings,
    http: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ttl_enabled: bool = True,
    sleep: Sleep = asyncio.sleep,
) -> Callable[[], MetadataGateway]:
    """Factory gateway cho `PipelineDeps.metadata`.

    Connector, cache và limiter dựng một lần (limiter dùng chung cả tiến trình); mỗi lần gọi
    trả gateway mới với bộ ngắt mạch mới. `ttl_enabled=False` cho DB đánh giá (preflight F17):
    dòng cache không hết hạn. `sleep` dùng cho cả limiter lẫn chờ thử lại (test truyền hàm
    không chờ).
    """
    timeout_s = settings.metadata_timeout_seconds
    api_key = settings.semantic_scholar_api_key
    connectors: dict[str, MetadataConnector] = {
        "crossref": CrossrefConnector(
            http, contact_email=settings.contact_email, timeout_s=timeout_s
        ),
        "openalex": OpenAlexConnector(
            http, contact_email=settings.contact_email, timeout_s=timeout_s
        ),
        "semantic_scholar": SemanticScholarConnector(
            http,
            api_key=api_key.get_secret_value() if api_key is not None else None,
            timeout_s=timeout_s,
        ),
    }
    limiters = {
        name: RateLimiter(
            settings.metadata_rate_limits.get(name, defaults.METADATA_RATE_LIMITS[name]),
            sleep=sleep,
        )
        for name in SOURCES
    }
    cache = MetadataCache(
        session_factory,
        ok_ttl=timedelta(days=settings.metadata_cache_ttl_days) if ttl_enabled else None,
        not_found_ttl=(
            timedelta(days=settings.metadata_not_found_ttl_days) if ttl_enabled else None
        ),
    )

    def factory() -> MetadataGateway:
        return MetadataGateway(
            connectors,
            cache,
            limiters,
            breaker_threshold=settings.metadata_breaker_threshold,
            sleep=sleep,
        )

    return factory
