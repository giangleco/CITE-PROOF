"""MetadataGateway: thử lại, Retry-After, ngắt mạch theo nguồn, khóa cache, factory (T-304)."""

from collections.abc import Sequence

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.domain import ParsedReference
from citeproof.core.errors import ExternalServiceError, InternalError, RetryableError
from citeproof.core.settings import Settings
from citeproof.integrations.metadata import (
    SOURCES,
    CircuitBreaker,
    CrossrefConnector,
    InMemoryMetadataCache,
    LookupOutcome,
    MetadataGateway,
    OpenAlexConnector,
    RateLimiter,
    SemanticScholarConnector,
    WorkRecord,
    make_gateway_factory,
)
from tests.integrations.metadata.fakes import FakeApi, json_response, load_fixture, sequence


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def _no_sleep(seconds: float) -> None:
    return None


def _gateway(
    api: FakeApi,
    *,
    sleep: SleepRecorder | None = None,
    cache: InMemoryMetadataCache | None = None,
    threshold: int = 3,
) -> MetadataGateway:
    http = api.client()
    return MetadataGateway(
        {
            "crossref": CrossrefConnector(http, contact_email=None),
            "openalex": OpenAlexConnector(http, contact_email=None),
            "semantic_scholar": SemanticScholarConnector(http, api_key=None),
        },
        cache or InMemoryMetadataCache(),
        {name: RateLimiter(1000.0, sleep=_no_sleep) for name in SOURCES},
        breaker_threshold=threshold,
        sleep=sleep or SleepRecorder(),
    )


HE = load_fixture("crossref_doi_he")


# --- Thử lại ----------------------------------------------------------------------------------


async def test_retry_after_honoured_and_capped() -> None:
    api = FakeApi(sequence(httpx.Response(429, headers={"Retry-After": "30"}), json_response(HE)))
    sleep = SleepRecorder()
    outcome = await _gateway(api, sleep=sleep).by_doi("crossref", "10.1109/cvpr.2016.90")

    assert outcome == LookupOutcome("crossref", "ok", outcome.records)
    assert outcome.records[0].title == "Deep Residual Learning for Image Recognition"
    assert sleep.calls == [10.0]
    assert api.calls == 2


async def test_short_retry_after_is_used_as_is() -> None:
    api = FakeApi(sequence(httpx.Response(429, headers={"Retry-After": "3"}), json_response(HE)))
    sleep = SleepRecorder()
    await _gateway(api, sleep=sleep).by_doi("crossref", "10.1109/cvpr.2016.90")
    assert sleep.calls == [3.0]


async def test_backoff_then_error_after_two_retries() -> None:
    api = FakeApi(sequence(httpx.Response(503)))
    sleep = SleepRecorder()
    outcome = await _gateway(api, sleep=sleep).by_doi("openalex", "10.1000/x")

    assert outcome == LookupOutcome("openalex", "error", [])
    assert sleep.calls == [1.0, 2.0]
    assert api.calls == 3  # lần đầu + 2 lần thử lại


async def test_timeout_then_success() -> None:
    api = FakeApi(sequence(httpx.ReadTimeout("slow"), json_response(HE)))
    sleep = SleepRecorder()
    outcome = await _gateway(api, sleep=sleep).by_doi("crossref", "10.1109/cvpr.2016.90")
    assert outcome.status == "ok"
    assert sleep.calls == [1.0]


async def test_non_retryable_error_is_not_retried() -> None:
    api = FakeApi(lambda request: httpx.Response(400))
    sleep = SleepRecorder()
    outcome = await _gateway(api, sleep=sleep).search_text(
        "semantic_scholar", "query", year=None, limit=5
    )
    assert outcome == LookupOutcome("semantic_scholar", "error", [])
    assert api.calls == 1
    assert sleep.calls == []


async def test_limiter_acquired_before_every_attempt() -> None:
    class CountingLimiter(RateLimiter):
        def __init__(self) -> None:
            super().__init__(1000.0, sleep=_no_sleep)
            self.acquired = 0

        async def acquire(self) -> None:
            self.acquired += 1
            await super().acquire()

    limiter = CountingLimiter()
    api = FakeApi(sequence(httpx.Response(500), httpx.Response(500), json_response(HE)))
    gateway = MetadataGateway(
        {"crossref": CrossrefConnector(api.client(), contact_email=None)},
        InMemoryMetadataCache(),
        {"crossref": limiter},
        breaker_threshold=3,
        sleep=_no_sleep,
    )
    assert (await gateway.by_doi("crossref", "10.1109/cvpr.2016.90")).status == "ok"
    assert limiter.acquired == api.calls == 3


# --- Ngắt mạch --------------------------------------------------------------------------------


def test_circuit_breaker_counts_consecutive_failures() -> None:
    breaker = CircuitBreaker(3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.is_open
    breaker.record_failure()
    assert breaker.is_open
    with pytest.raises(ValueError):
        CircuitBreaker(0)


async def test_breaker_opens_after_three_failures() -> None:
    api = FakeApi(lambda request: httpx.Response(500))
    gateway = _gateway(api)
    for doi in ("10.1000/a", "10.1000/b", "10.1000/c"):
        assert (await gateway.by_doi("crossref", doi)).status == "error"
    calls = api.calls

    fourth = await gateway.by_doi("crossref", "10.1000/d")

    assert fourth == LookupOutcome("crossref", "circuit_open", [])
    assert api.calls == calls  # không gọi HTTP khi mạch đã ngắt
    assert not gateway.available("crossref")
    assert gateway.available("openalex")
    assert gateway.available("semantic_scholar")


async def test_success_resets_breaker() -> None:
    responses: list[httpx.Response] = [
        httpx.Response(400),
        httpx.Response(400),
        httpx.Response(404),
        httpx.Response(400),
        httpx.Response(400),
    ]
    api = FakeApi(sequence(*responses))
    gateway = _gateway(api)
    statuses = [(await gateway.by_doi("crossref", f"10.1000/{n}")).status for n in range(5)]
    assert statuses == ["error", "error", "not_found", "error", "error"]
    assert gateway.available("crossref")


async def test_breaker_checked_before_cache() -> None:
    cache = InMemoryMetadataCache()
    ok = FakeApi(lambda request: json_response(HE))
    assert (
        await _gateway(ok, cache=cache).by_doi("crossref", "10.1109/cvpr.2016.90")
    ).status == "ok"
    failing = FakeApi(lambda request: httpx.Response(400))
    gateway = _gateway(failing, cache=cache, threshold=1)
    assert (await gateway.by_doi("crossref", "10.1000/other")).status == "error"
    # Thứ tự của brief: ngắt mạch → cache → giới hạn tốc độ → connector.
    cached = await gateway.by_doi("crossref", "10.1109/cvpr.2016.90")
    assert cached.status == "circuit_open"


# --- Kết quả và khóa cache --------------------------------------------------------------------


async def test_statuses_ok_and_not_found_are_cached() -> None:
    cache = InMemoryMetadataCache()
    api = FakeApi(lambda request: httpx.Response(404))
    gateway = _gateway(api, cache=cache)
    assert (await gateway.by_doi("semantic_scholar", "10.1000/x")).status == "not_found"
    assert (await gateway.by_doi("semantic_scholar", "10.1000/X")).status == "not_found"
    assert api.calls == 1
    assert (await gateway.by_doi("openalex", "10.1000/x")).status == "not_found"
    assert api.calls == 2  # khóa cache gồm nguồn


async def test_invalid_doi_is_not_found_without_http() -> None:
    api = FakeApi(lambda request: json_response(HE))
    outcome = await _gateway(api).by_doi("crossref", "not a doi")
    assert outcome == LookupOutcome("crossref", "not_found", [])
    assert api.calls == 0


async def test_search_cache_key_ignores_label_and_url() -> None:
    fixture = load_fixture("crossref_search_hochreiter")
    api = FakeApi(lambda request: json_response(fixture))
    gateway = _gateway(api)
    base = {"title": "Long short-term memory", "authors": ["Hochreiter, S."], "year": 1997}
    first = await gateway.search("crossref", ParsedReference(**base, label="[3]", url="http://a"))
    second = await gateway.search("crossref", ParsedReference(**base, label="7"))
    other_year = await gateway.search("crossref", ParsedReference(**{**base, "year": 1998}))

    assert first.status == second.status == other_year.status == "ok"
    assert api.calls == 2
    limited = await gateway.search("crossref", ParsedReference(**base), limit=2)
    assert limited.status == "ok"
    assert api.calls == 3  # limit thuộc khóa cache


async def test_search_text_cached_per_year() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("openalex_search_he")))
    gateway = _gateway(api)
    await gateway.search_text("openalex", "residual learning", year=2016, limit=5)
    await gateway.search_text("openalex", "residual learning", year=2016, limit=5)
    await gateway.search_text("openalex", "residual learning", year=None, limit=5)
    assert api.calls == 2


async def test_unknown_source_is_internal_error() -> None:
    gateway = _gateway(FakeApi(lambda request: httpx.Response(404)))
    with pytest.raises(InternalError):
        await gateway.by_doi("arxiv", "10.1000/x")
    assert not gateway.available("arxiv")


class _RaisingConnector:
    name = "crossref"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def search(self, ref: ParsedReference, limit: int = 5) -> list[WorkRecord]:
        raise self.error

    async def search_text(
        self, query: str, *, year: int | None = None, limit: int = 5
    ) -> list[WorkRecord]:
        raise self.error

    async def get_by_doi(self, doi: str) -> WorkRecord | None:
        raise self.error


def _raising_gateway(error: Exception, sleep: SleepRecorder) -> MetadataGateway:
    return MetadataGateway(
        {"crossref": _RaisingConnector(error)},
        InMemoryMetadataCache(),
        {},
        breaker_threshold=3,
        sleep=sleep,
    )


async def test_programming_errors_propagate() -> None:
    gateway = _raising_gateway(InternalError("bug"), SleepRecorder())
    with pytest.raises(InternalError):
        await gateway.search_text("crossref", "q", year=None, limit=5)
    assert gateway.available("crossref")


@pytest.mark.parametrize(
    ("error", "sleeps"),
    [
        (ExternalServiceError("x"), []),
        (RetryableError("x", retry_after=0.5), [0.5, 0.5]),
    ],
)
async def test_connector_errors_become_error_outcome(
    error: Exception, sleeps: Sequence[float]
) -> None:
    sleep = SleepRecorder()
    outcome = await _raising_gateway(error, sleep).search("crossref", ParsedReference(title="t"))
    assert outcome == LookupOutcome("crossref", "error", [])
    assert sleep.calls == list(sleeps)


async def test_error_log_has_no_query(caplog: pytest.LogCaptureFixture) -> None:
    api = FakeApi(lambda request: httpx.Response(400))
    secret = "Phân loại văn bản tiếng Việt"
    with caplog.at_level("DEBUG"):
        await _gateway(api).search("openalex", ParsedReference(title=secret))
    # Logger httpx (ghi cả URL) bị chặn ở `core.logging`; ở đây chỉ xét log của CiteProof.
    ours = [r for r in caplog.records if r.name.startswith("citeproof")]
    assert ours
    for record in ours:
        assert secret not in record.getMessage()
        assert "search=" not in record.getMessage()
        assert "openalex" in record.getMessage()


# --- Factory ----------------------------------------------------------------------------------


async def test_factory_shares_limiters_and_resets_breakers(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        semantic_scholar_api_key="s2-key",
        contact_email="ops@example.org",
        metadata_breaker_threshold=1,
        metadata_rate_limits={"crossref": 2.0},
    )
    api = FakeApi(lambda request: httpx.Response(400))
    factory = make_gateway_factory(settings, api.client(), session_factory, sleep=_no_sleep)
    first, second = factory(), factory()

    assert first.limiters is second.limiters
    assert set(first.limiters) == set(SOURCES)
    assert first.limiters["crossref"].rate_per_second == 2.0
    assert first.limiters["openalex"].rate_per_second == 10.0  # khóa thiếu: mặc định trong mã
    assert first.limiters["semantic_scholar"].rate_per_second == 0.3
    assert first.sources == SOURCES

    await first.search_text("semantic_scholar", "q", year=None, limit=5)
    assert not first.available("semantic_scholar")
    assert second.available("semantic_scholar")  # bộ ngắt mạch mới cho mỗi lần chạy
    (request,) = api.requests
    assert request.headers["x-api-key"] == "s2-key"
    assert "@" not in request.headers["User-Agent"]  # S2 không nhận email liên hệ
