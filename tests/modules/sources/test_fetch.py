"""Tải toàn văn mở qua `SafeHttpClient` (spec §7.5, SR-11).

Mọi request đi qua `SafeHttpClient` với resolver giả và `httpx.MockTransport`.
"""

import hashlib

import httpx
import pytest

from citeproof.integrations.http_safe import SafeHttpClient
from citeproof.integrations.oa import AccessOption
from citeproof.modules.sources.fetch import FetchedFile, FullTextFetcher, open_source_key
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
