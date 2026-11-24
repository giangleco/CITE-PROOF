"""Kiểm tra URL của tài liệu web và tra bản lưu trữ archive.org (FR-15, T-303).

Mọi request đi qua `SafeHttpClient` với resolver giả và `httpx.MockTransport`.
"""

import json
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from citeproof.integrations.http_safe import SafeHttpClient
from citeproof.modules.references.web_check import ARCHIVE_API, archive_lookup, check_url
from tests.pipeline.helpers import public_resolver

PAGE = "https://example.org/bao-cao?nam=2024"


def stream_response(
    status: int, content: bytes = b"", headers: dict[str, str] | None = None
) -> httpx.Response:
    """Response chưa đọc (như từ transport thật), để `SafeHttpClient` đọc dần được."""
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(content))


def json_stream(data: object, status: int = 200) -> httpx.Response:
    body = json.dumps(data).encode()
    return stream_response(status, body, {"content-type": "application/json"})


class Recorder:
    """Handler trả cùng một câu trả lời (dựng mới mỗi lần) và ghi lại request."""

    def __init__(self, response: Callable[[], httpx.Response] | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response()

    def client(self) -> SafeHttpClient:
        return SafeHttpClient(resolver=public_resolver, transport=httpx.MockTransport(self))


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, True), (204, True), (304, True), (403, False), (404, False), (500, False)],
)
async def test_check_url_status(status: int, expected: bool) -> None:
    recorder = Recorder(lambda: stream_response(status, b"..."))
    assert await check_url(recorder.client(), PAGE) is expected
    assert recorder.requests[0].headers["host"] == "example.org"


async def test_check_url_follows_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cu":
            return stream_response(301, headers={"location": "https://example.org/moi"})
        return stream_response(404)

    client = SafeHttpClient(resolver=public_resolver, transport=httpx.MockTransport(handler))
    assert await check_url(client, "https://example.org/cu") is False


async def test_reference_url_to_metadata_ip_blocked() -> None:
    recorder = Recorder(lambda: stream_response(200))
    assert await check_url(recorder.client(), "http://169.254.169.254/latest") is None
    assert recorder.requests == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost/admin", "khong-phai-url"])
async def test_unsafe_or_invalid_url_is_unknown(url: str) -> None:
    async def local_resolver(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    recorder = Recorder(lambda: stream_response(200))
    client = SafeHttpClient(resolver=local_resolver, transport=httpx.MockTransport(recorder))
    assert await check_url(client, url) is None
    assert recorder.requests == []


async def test_network_error_is_unknown() -> None:
    recorder = Recorder(httpx.ConnectError("refused"))
    assert await check_url(recorder.client(), PAGE) is None


def _archive(snapshot: dict[str, object] | None) -> Callable[[], httpx.Response]:
    body = {"url": PAGE, "archived_snapshots": {} if snapshot is None else {"closest": snapshot}}
    return lambda: json_stream(body)


async def test_archive_lookup_returns_snapshot() -> None:
    snapshot = "http://web.archive.org/web/20240101000000/https://example.org/bao-cao?nam=2024"
    recorder = Recorder(_archive({"available": True, "status": "200", "url": snapshot}))
    assert await archive_lookup(recorder.client(), PAGE) == snapshot
    request = recorder.requests[0]
    assert request.headers["host"] == urlsplit(ARCHIVE_API).hostname == "archive.org"
    assert request.url.path == "/wayback/available"
    assert parse_qs(request.url.query.decode())["url"] == [PAGE]


@pytest.mark.parametrize(
    "response",
    [
        _archive(None),
        _archive({"available": False, "url": "http://web.archive.org/web/1/x"}),
        _archive({"available": True, "url": "https://evil.example/web/1/x"}),
        _archive({"available": True, "url": "javascript:alert(1)"}),
        lambda: stream_response(200, "không phải JSON".encode()),
        lambda: json_stream([1, 2]),
        lambda: stream_response(503),
    ],
)
async def test_archive_lookup_none_when_unavailable(
    response: Callable[[], httpx.Response],
) -> None:
    assert await archive_lookup(Recorder(response).client(), PAGE) is None


async def test_archive_lookup_network_error_is_none() -> None:
    assert await archive_lookup(Recorder(httpx.ConnectError("x")).client(), PAGE) is None
