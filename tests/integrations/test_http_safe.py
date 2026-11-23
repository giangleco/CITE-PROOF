import asyncio
import gzip
import tracemalloc
import zlib
from collections.abc import Callable

import httpx
import pytest

from citeproof.core.errors import RetryableError
from citeproof.integrations.http_safe import (
    FetchError,
    FetchFailedError,
    SafeHttpClient,
    UnsafeUrlError,
)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for c in self.chunks:
            self.yielded += 1
            yield c


def make_response(
    status: int,
    *,
    headers: dict[str, str] | None = None,
    content: bytes = b"",
    stream: ChunkStream | None = None,
) -> httpx.Response:
    """Response chưa đọc, để aiter_raw hoạt động như với transport thật."""
    return httpx.Response(status, headers=headers, stream=stream or ChunkStream([content]))


PUBLIC_IP = "93.184.216.34"
HOSTS = {
    "example.org": [PUBLIC_IP],
    "localhost": ["127.0.0.1"],
    "mixed.example": [PUBLIC_IP, "10.0.0.1"],
    "empty.example": [],
    "xn--bcher-kva.example": [PUBLIC_IP],
    "v6.example": ["2606:2800:220:1:248:1893:25c8:1946"],
}


async def resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda r: make_response(200, content=b"ok"))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def client(self, *, max_bytes: int = 5_000_000, max_redirects: int = 5) -> SafeHttpClient:
        return SafeHttpClient(
            resolver=resolver,
            transport=httpx.MockTransport(self),
            max_bytes=max_bytes,
            max_redirects=max_redirects,
        )


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://x"])
async def test_scheme_blocked(url: str) -> None:
    rec = Recorder()
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get(url)
    assert ei.value.code == "SCHEME_NOT_ALLOWED"
    assert not rec.requests


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:5432/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://127.1/",
        "http://10.0.0.5/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
        "http://[2002:7f00:1::]/",
        "http://mixed.example/",
        "http://0.0.0.0/",
        "http://[::]/",
        "http://localhost./",
        "http://127.0.0.1./",
        "http://[::7f00:1]/",
        "http://[::ffff:0:7f00:1]/",
        "http://[fec0::1]/",
        "http://[fe80::1%25eth0]/",
        "http://１２７.0.0.1/",  # noqa: RUF001
    ],
)
async def test_private_targets_blocked(url: str) -> None:
    rec = Recorder()
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get(url)
    assert ei.value.code == "HOST_NOT_ALLOWED"
    assert not rec.requests


async def test_literal_ip_never_resolved() -> None:
    calls: list[str] = []

    async def spy(host: str, port: int) -> list[str]:
        calls.append(host)
        return [PUBLIC_IP]

    client = SafeHttpClient(resolver=spy, transport=httpx.MockTransport(Recorder()))
    with pytest.raises(UnsafeUrlError):
        await client.get("http://0x7f.0.0.1/")
    assert calls == []


async def test_empty_resolution_is_dns_failed() -> None:
    with pytest.raises(UnsafeUrlError) as ei:
        await Recorder().client().get("http://empty.example/")
    assert ei.value.code == "DNS_FAILED"


async def test_resolver_oserror_is_dns_failed() -> None:
    async def boom(host: str, port: int) -> list[str]:
        raise OSError("nope")

    client = SafeHttpClient(resolver=boom, transport=httpx.MockTransport(Recorder()))
    with pytest.raises(UnsafeUrlError) as ei:
        await client.get("http://example.org/")
    assert ei.value.code == "DNS_FAILED"


async def test_credentials_rejected() -> None:
    rec = Recorder()
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get("http://user:pass@example.org/")
    assert ei.value.code == "CREDENTIALS_NOT_ALLOWED"
    assert not rec.requests


async def test_redirect_to_private_blocked_and_never_requested() -> None:
    rec = Recorder(lambda r: make_response(302, headers={"location": "http://127.0.0.1/admin"}))
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get("http://example.org/start")
    assert ei.value.code == "HOST_NOT_ALLOWED"
    assert [r.url.host for r in rec.requests] == [PUBLIC_IP]


async def test_relative_redirect_followed() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/a/b":
            return make_response(301, headers={"location": "../c?x=1"})
        return make_response(200, content=b"done")

    rec = Recorder(handler)
    resp = await rec.client().get("http://example.org/a/b")
    assert resp.body == b"done"
    assert resp.final_url == "http://example.org/c?x=1"


async def test_connects_to_validated_ip_with_host_header() -> None:
    rec = Recorder()
    await rec.client().get("http://example.org/p?q=1")
    req = rec.requests[0]
    assert req.url.host == PUBLIC_IP
    assert req.url.path == "/p"
    assert req.url.query == b"q=1"
    assert req.headers["host"] == "example.org"


async def test_non_default_port_in_host_header_and_sni() -> None:
    rec = Recorder()
    await rec.client().get("https://example.org:8443/")
    req = rec.requests[0]
    assert req.headers["host"] == "example.org:8443"
    assert req.url.port == 8443
    assert req.extensions["sni_hostname"] == "example.org"


async def test_body_capped() -> None:
    rec = Recorder(lambda r: make_response(200, content=b"x" * 10_000))
    resp = await rec.client(max_bytes=1000).get("http://example.org/")
    assert len(resp.body) == 1000
    assert resp.truncated


async def test_body_not_truncated_when_small() -> None:
    resp = await Recorder().client().get("http://example.org/")
    assert resp.body == b"ok"
    assert not resp.truncated
    assert resp.status_code == 200


async def test_too_many_redirects() -> None:
    rec = Recorder(lambda r: make_response(302, headers={"location": "http://example.org/n"}))
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client(max_redirects=3).get("http://example.org/")
    assert ei.value.code == "TOO_MANY_REDIRECTS"
    assert len(rec.requests) == 4


async def test_no_cookie_or_auth_headers_sent() -> None:
    rec = Recorder(lambda r: make_response(200, headers={"set-cookie": "a=b"}))
    client = rec.client()
    await client.get("http://example.org/")
    await client.get("http://example.org/")
    for req in rec.requests:
        names = {k.lower() for k in req.headers}
        assert not names & {"cookie", "authorization", "referer", "proxy-authorization"}
        assert req.headers["user-agent"] == "CiteProof/0.1"


async def test_timeout_and_connect_errors_are_fetch_errors_without_query() -> None:
    def timeout(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    def refused(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=r)

    for handler in (timeout, refused):
        with pytest.raises(FetchError) as ei:
            await Recorder(handler).client().get("http://example.org/p?token=SECRET")
        assert isinstance(ei.value, RetryableError)
        assert "SECRET" not in str(ei.value)
        assert "example.org" in str(ei.value)


async def test_exact_header_set() -> None:
    rec = Recorder()
    await rec.client().get("http://example.org/")
    assert dict(rec.requests[0].headers) == {
        "host": "example.org",
        "user-agent": "CiteProof/0.1",
        "accept": "*/*",
        "accept-encoding": "identity",
    }


async def test_gzip_bomb_is_bounded() -> None:
    bomb = gzip.compress(b"\0" * 10_000_000)
    rec = Recorder(lambda r: make_response(200, headers={"content-encoding": "gzip"}, content=bomb))
    tracemalloc.start()
    try:
        resp = await rec.client(max_bytes=1000).get("http://example.org/")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(resp.body) == 1000
    assert resp.truncated
    assert peak < 5_000_000


async def test_deflate_decoded_and_unknown_encoding_rejected() -> None:
    rec = Recorder(
        lambda r: make_response(
            200, headers={"content-encoding": "deflate"}, content=zlib.compress(b"hello")
        )
    )
    resp = await rec.client().get("http://example.org/")
    assert resp.body == b"hello"
    assert not resp.truncated
    rec = Recorder(lambda r: make_response(200, headers={"content-encoding": "br"}, content=b"x"))
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get("http://example.org/")
    assert ei.value.code == "UNSUPPORTED_CONTENT_ENCODING"


async def test_multi_chunk_stream_stops_early() -> None:
    stream = ChunkStream([b"a" * 400] * 100)
    rec = Recorder(lambda r: make_response(200, stream=stream))
    resp = await rec.client(max_bytes=1000).get("http://example.org/")
    assert len(resp.body) == 1000
    assert resp.truncated
    assert stream.yielded <= 4


async def test_total_deadline_resolver_and_transport() -> None:
    async def slow_resolver(host: str, port: int) -> list[str]:
        await asyncio.sleep(5)
        return [PUBLIC_IP]

    client = SafeHttpClient(
        resolver=slow_resolver, transport=httpx.MockTransport(Recorder()), total_timeout_s=0.05
    )
    with pytest.raises(FetchError) as ei:
        await client.get("http://example.org/p?token=SECRET")
    assert "SECRET" not in str(ei.value)

    async def slow_handler(r: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return make_response(200)

    client = SafeHttpClient(
        resolver=resolver, transport=httpx.MockTransport(slow_handler), total_timeout_s=0.05
    )
    with pytest.raises(FetchError):
        await client.get("http://example.org/")


async def test_idna_host_encoded_for_resolver_and_host_header() -> None:
    rec = Recorder()
    await rec.client().get("http://bücher.example/")
    assert rec.requests[0].headers["host"] == "xn--bcher-kva.example"


async def test_invalid_hosts_and_urls() -> None:
    for url in ("http://a..b/", "http://" + "a" * 64 + ".example/"):
        with pytest.raises(UnsafeUrlError) as ei:
            await Recorder().client().get(url)
        assert ei.value.code == "HOST_NOT_ALLOWED"
    for url in (
        "http://example.org/a\x00b",
        "http://example.org/a\nb",
        "http://example.org:0/",
        "http://example.org:99999/",
        "http://example.org/" + "a" * 70_000,
    ):
        with pytest.raises(UnsafeUrlError) as ei:
            await Recorder().client().get(url)
        assert ei.value.code == "INVALID_URL"


@pytest.mark.parametrize("status", [303, 307, 308])
async def test_other_redirect_statuses_followed(status: int) -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/":
            return make_response(status, headers={"location": "/next"})
        return make_response(200, content=b"end")

    resp = await Recorder(handler).client().get("http://example.org/")
    assert resp.body == b"end"


async def test_scheme_relative_redirect_to_loopback_blocked() -> None:
    rec = Recorder(lambda r: make_response(302, headers={"location": "//127.0.0.1/"}))
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get("http://example.org/")
    assert ei.value.code == "HOST_NOT_ALLOWED"
    assert len(rec.requests) == 1


async def test_redirect_without_location_returned_with_body() -> None:
    rec = Recorder(lambda r: make_response(302, content=b"moved"))
    resp = await rec.client().get("http://example.org/")
    assert resp.status_code == 302
    assert resp.body == b"moved"


async def test_resolved_ipv6_https_default_port_sni() -> None:
    rec = Recorder()
    await rec.client().get("https://v6.example/")
    req = rec.requests[0]
    assert req.url.host == "2606:2800:220:1:248:1893:25c8:1946"
    assert req.extensions["sni_hostname"] == "v6.example"
    assert req.headers["host"] == "v6.example"


async def test_proxy_env_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    rec = Recorder()
    resp = await rec.client().get("https://example.org/")
    assert resp.status_code == 200
    assert rec.requests[0].url.host == PUBLIC_IP


async def test_non_network_http_error_is_non_retryable() -> None:
    def bad(r: httpx.Request) -> httpx.Response:
        raise httpx.ProtocolError("bad", request=r)

    with pytest.raises(FetchFailedError) as ei:
        await Recorder(bad).client().get("http://example.org/?q=SECRET")
    assert not isinstance(ei.value, RetryableError)
    assert "SECRET" not in str(ei.value)


@pytest.mark.parametrize("url", ["http://[v1..:]/", "http://[v7.abc]/"])
async def test_ipvfuture_hosts_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError) as ei:
        await Recorder().client().get(url)
    assert ei.value.code == "HOST_NOT_ALLOWED"


async def test_resolver_unicode_error_is_dns_failed() -> None:
    async def boom(host: str, port: int) -> list[str]:
        raise UnicodeError("bad label")

    client = SafeHttpClient(resolver=boom, transport=httpx.MockTransport(Recorder()))
    with pytest.raises(UnsafeUrlError) as ei:
        await client.get("http://example.org/")
    assert ei.value.code == "DNS_FAILED"


async def test_many_tiny_chunks_bounded() -> None:
    rec = Recorder(lambda r: make_response(200, stream=ChunkStream([b"ab"] * 5000)))
    resp = await rec.client(max_bytes=1000).get("http://example.org/")
    assert len(resp.body) == 1000
    assert resp.truncated


async def test_incomplete_gzip_is_truncated() -> None:
    data = gzip.compress(b"hello world" * 100)[:-12]
    rec = Recorder(lambda r: make_response(200, headers={"content-encoding": "gzip"}, content=data))
    resp = await rec.client().get("http://example.org/")
    assert resp.truncated


async def test_concatenated_gzip_members_truncated() -> None:
    data = gzip.compress(b"first") + gzip.compress(b"second")
    rec = Recorder(lambda r: make_response(200, headers={"content-encoding": "gzip"}, content=data))
    resp = await rec.client().get("http://example.org/")
    assert resp.body == b"first"
    assert resp.truncated


async def test_data_after_eof_in_later_chunk_truncated() -> None:
    gz = gzip.compress(b"first")
    rec = Recorder(
        lambda r: make_response(
            200, headers={"content-encoding": "gzip"}, stream=ChunkStream([gz, b"extra"])
        )
    )
    resp = await rec.client().get("http://example.org/")
    assert resp.truncated


async def test_complete_gzip_not_truncated() -> None:
    rec = Recorder(
        lambda r: make_response(
            200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"hello")
        )
    )
    resp = await rec.client().get("http://example.org/")
    assert resp.body == b"hello"
    assert not resp.truncated


async def test_underscore_host_allowed() -> None:
    hosts = HOSTS | {"my_host.example.org": [PUBLIC_IP]}

    async def res(host: str, port: int) -> list[str]:
        return hosts[host]

    rec = Recorder()
    client = SafeHttpClient(resolver=res, transport=httpx.MockTransport(rec))
    await client.get("http://My_Host.example.org/")
    assert rec.requests[0].headers["host"] == "my_host.example.org"
