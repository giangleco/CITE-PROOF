from collections.abc import Callable

import httpx
import pytest

from citeproof.core.errors import RetryableError
from citeproof.integrations.http_safe import FetchError, SafeHttpClient, UnsafeUrlError

PUBLIC_IP = "93.184.216.34"
HOSTS = {
    "example.org": [PUBLIC_IP],
    "localhost": ["127.0.0.1"],
    "mixed.example": [PUBLIC_IP, "10.0.0.1"],
    "empty.example": [],
}


async def resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda r: httpx.Response(200, content=b"ok"))

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
    rec = Recorder(lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}))
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client().get("http://example.org/start")
    assert ei.value.code == "HOST_NOT_ALLOWED"
    assert [r.url.host for r in rec.requests] == [PUBLIC_IP]


async def test_relative_redirect_followed() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/a/b":
            return httpx.Response(301, headers={"location": "../c?x=1"})
        return httpx.Response(200, content=b"done")

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
    rec = Recorder(lambda r: httpx.Response(200, content=b"x" * 10_000))
    resp = await rec.client(max_bytes=1000).get("http://example.org/")
    assert len(resp.body) == 1000
    assert resp.truncated


async def test_body_not_truncated_when_small() -> None:
    resp = await Recorder().client().get("http://example.org/")
    assert resp.body == b"ok"
    assert not resp.truncated
    assert resp.status_code == 200


async def test_too_many_redirects() -> None:
    rec = Recorder(lambda r: httpx.Response(302, headers={"location": "http://example.org/n"}))
    with pytest.raises(UnsafeUrlError) as ei:
        await rec.client(max_redirects=3).get("http://example.org/")
    assert ei.value.code == "TOO_MANY_REDIRECTS"
    assert len(rec.requests) == 4


async def test_no_cookie_or_auth_headers_sent() -> None:
    rec = Recorder(lambda r: httpx.Response(200, headers={"set-cookie": "a=b"}))
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
