"""Tải toàn văn mở qua `SafeHttpClient` (spec §7.5, SR-11).

Mọi request đi qua `SafeHttpClient` với resolver giả và `httpx.MockTransport`.
"""

import asyncio
import hashlib

import httpx
import pytest

from citeproof.integrations.http_safe import SafeHttpClient
from citeproof.integrations.oa import AccessOption, HostRateLimits
from citeproof.modules.sources.fetch import FetchedFile, FullTextFetcher, open_source_key
from tests.integrations.metadata.test_metadata_ratelimit import FakeClock
from tests.integrations.oa.fakes import OPEN_PDF, html_stream, pdf_stream
from tests.modules.references.test_web_check import Recorder
from tests.pipeline.helpers import public_resolver

OPTION = AccessOption(url="https://export.arxiv.org/pdf/1706.03762", license=None, provider="arxiv")


def _client(recorder: Recorder, *, max_bytes: int = 31_457_280) -> SafeHttpClient:
    return SafeHttpClient(
        resolver=public_resolver, transport=httpx.MockTransport(recorder), max_bytes=max_bytes
    )


def test_open_source_key() -> None:
    sha = "ab" * 32
    assert open_source_key(sha, "pdf") == f"sources/open/{sha}.pdf"
    assert open_source_key(sha, "abstract.json") == f"sources/open/{sha}.abstract.json"


async def test_fetch_accepts_pdf() -> None:
    recorder = Recorder(pdf_stream)
    fetched = await FullTextFetcher(_client(recorder)).fetch(OPTION)
    assert fetched == FetchedFile(
        data=OPEN_PDF, url=OPTION.url, sha256=hashlib.sha256(OPEN_PDF).hexdigest()
    )
    [request] = recorder.requests
    assert request.headers["host"] == "export.arxiv.org"


async def test_fetch_follows_redirect_to_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pdf/1706.03762":
            return httpx.Response(302, headers={"location": "/pdf/1706.03762v7"})
        return pdf_stream()

    client = SafeHttpClient(resolver=public_resolver, transport=httpx.MockTransport(handler))
    fetched = await FullTextFetcher(client).fetch(OPTION)
    assert fetched is not None
    assert fetched.url == OPTION.url  # URL đã chọn, không phải URL sau chuyển hướng


async def test_fetch_rejects_html() -> None:
    recorder = Recorder(html_stream)
    assert await FullTextFetcher(_client(recorder)).fetch(OPTION) is None


async def test_fetch_rejects_truncated() -> None:
    recorder = Recorder(pdf_stream)
    assert await FullTextFetcher(_client(recorder, max_bytes=100)).fetch(OPTION) is None
    assert len(OPEN_PDF) > 100


@pytest.mark.parametrize("status", [201, 206, 404, 500])
async def test_fetch_rejects_non_200(status: int) -> None:
    recorder = Recorder(lambda: pdf_stream(status=status))
    assert await FullTextFetcher(_client(recorder)).fetch(OPTION) is None


async def test_fetch_errors_give_none() -> None:
    network = Recorder(httpx.ConnectError("boom"))
    assert await FullTextFetcher(_client(network)).fetch(OPTION) is None
    protocol = Recorder(httpx.DecodingError("bad"))
    assert await FullTextFetcher(_client(protocol)).fetch(OPTION) is None


async def test_fetch_private_ip_blocked_without_request() -> None:
    recorder = Recorder(pdf_stream)
    option = AccessOption(url="http://169.254.169.254/x", license=None, provider="openalex")
    assert await FullTextFetcher(_client(recorder)).fetch(option) is None
    assert recorder.requests == []


# --- Giới hạn tốc độ theo host ---------------------------------------------------------------


def _timed_fetcher(clock: FakeClock) -> tuple[FullTextFetcher, list[tuple[str, float]]]:
    """Fetcher có giới hạn theo host trên đồng hồ giả; ghi (host, thời điểm) mỗi request."""
    seen: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["host"], clock.now))
        return pdf_stream()

    client = SafeHttpClient(resolver=public_resolver, transport=httpx.MockTransport(handler))
    limits = HostRateLimits(clock=clock, sleep=clock.sleep)
    return FullTextFetcher(client, limits=limits), seen


def _option(url: str) -> AccessOption:
    return AccessOption(url=url, license=None, provider="openalex")


async def test_concurrent_arxiv_fetches_spaced_and_acl_not_delayed() -> None:
    clock = FakeClock()
    fetcher, seen = _timed_fetcher(clock)
    first, second = await asyncio.gather(
        fetcher.fetch(OPTION), fetcher.fetch(_option("https://arxiv.org/pdf/1810.04805"))
    )
    assert first is not None and second is not None
    assert [host for host, _ in seen] == ["export.arxiv.org", "arxiv.org"]
    assert seen[1][1] - seen[0][1] >= 3.0  # hai host của arXiv chung một giới hạn
    # arXiv chỉ được gọi lại ở t = 6; ACL Anthology dùng giới hạn riêng nên đi ngay.
    now = clock.now
    assert await fetcher.fetch(_option("https://aclanthology.org/N19-1423.pdf")) is not None
    assert seen[-1] == ("aclanthology.org", now)
    await fetcher.fetch(_option("https://aclanthology.org/2020.acl-main.1.pdf"))
    assert seen[-1] == ("aclanthology.org", now + 1.0)


async def test_unlimited_hosts_not_delayed() -> None:
    clock = FakeClock()
    fetcher, seen = _timed_fetcher(clock)
    await asyncio.gather(
        *(fetcher.fetch(_option(f"https://www.nature.com/{i}.pdf")) for i in range(3))
    )
    assert [at for _, at in seen] == [0.0, 0.0, 0.0]
    assert clock.sleeps == []
