"""HTTP client chống SSRF (docs/07a §4.9, rủi ro SR-11).

Mọi URL suy ra từ dữ liệu người dùng chỉ được tải qua client này.
"""

import asyncio
import ipaddress
import re
import socket
import zlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
import idna

from citeproof.core.errors import ExternalServiceError, InputError, RetryableError

UnsafeUrlCode = Literal[
    "SCHEME_NOT_ALLOWED",
    "HOST_NOT_ALLOWED",
    "DNS_FAILED",
    "TOO_MANY_REDIRECTS",
    "CREDENTIALS_NOT_ALLOWED",
    "INVALID_URL",
    "UNSUPPORTED_CONTENT_ENCODING",
]

Resolver = Callable[[str, int], Awaitable[list[str]]]
_IP = ipaddress.IPv4Address | ipaddress.IPv6Address

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LEGACY_IPV4_RE = re.compile(r"^[0-9a-fA-FxX.]+$")
_USER_AGENT = "CiteProof/0.1"
_MAX_URL_LEN = 65_536
_GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")


class UnsafeUrlError(InputError):
    """URL bị từ chối vì không an toàn."""

    def __init__(self, code: UnsafeUrlCode, message: str) -> None:
        super().__init__(message)
        self.code: UnsafeUrlCode = code


class FetchError(RetryableError):
    """Timeout hoặc lỗi mạng (thông điệp không chứa query string)."""


class FetchFailedError(ExternalServiceError):
    """Lỗi HTTP khác, không nên thử lại (thông điệp không chứa query string)."""


@dataclass(frozen=True)
class SafeResponse:
    status_code: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes
    truncated: bool


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str  # ASCII/IDNA hoặc IP chuẩn hoá (IPv6 không có ngoặc)
    ip: str
    port: int
    path: str
    query: str

    @property
    def label(self) -> str:
        return f"{self.scheme}://{self.host}"


@dataclass(frozen=True)
class _Hop:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    truncated: bool


async def _default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _is_allowed_ip(ip: _IP) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_allowed_ip(ip.ipv4_mapped)
        embedded: list[ipaddress.IPv4Address] = []
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if not all(_is_allowed_ip(e) for e in embedded):
            return False
        if ip not in _GLOBAL_UNICAST_V6:
            return False
    return ip.is_global and not ip.is_multicast


def _parse_ip_literal(host: str) -> _IP | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _LEGACY_IPV4_RE.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _fmt_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _safe_label(url: str) -> str:
    """Chỉ scheme + host, an toàn để ghi log."""
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.hostname or '?'}"
    except ValueError:
        return "url"


def _bare(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """Bỏ header mặc định của httpx; chỉ gửi header ta tự đặt."""
    client.headers.clear()
    return client


def _make_decoder(headers: Mapping[str, str]) -> "zlib._Decompress | None":
    encoding = headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        return None
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        return zlib.decompressobj(zlib.MAX_WBITS)
    raise UnsafeUrlError(
        "UNSUPPORTED_CONTENT_ENCODING", f"Content-Encoding không hỗ trợ: {encoding[:32]}"
    )


class SafeHttpClient:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 10.0,
        total_timeout_s: float = 30.0,
        max_bytes: int = 5_000_000,
        max_redirects: int = 5,
    ) -> None:
        self._resolver = resolver or _default_resolver
        self._transport = transport
        self._timeout_s = timeout_s
        self._total_timeout_s = total_timeout_s
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def _validate(self, url: str) -> _Target:
        if len(url) > _MAX_URL_LEN or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
            raise UnsafeUrlError("INVALID_URL", "URL chứa ký tự không hợp lệ hoặc quá dài")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise UnsafeUrlError("INVALID_URL", "URL không hợp lệ") from exc
        if parts.scheme not in _DEFAULT_PORTS:
            raise UnsafeUrlError(
                "SCHEME_NOT_ALLOWED", f"Scheme không được phép: {parts.scheme[:16]!r}"
            )
        if "@" in parts.netloc:
            raise UnsafeUrlError("CREDENTIALS_NOT_ALLOWED", "URL không được chứa userinfo")
        if port is None:
            port = _DEFAULT_PORTS[parts.scheme]
        elif not 1 <= port <= 65535:
            raise UnsafeUrlError("INVALID_URL", "Port không hợp lệ")
        raw_host = parts.hostname
        if not raw_host:
            raise UnsafeUrlError("HOST_NOT_ALLOWED", "URL thiếu host")
        if "%" in raw_host:
            raise UnsafeUrlError("HOST_NOT_ALLOWED", "Host không được chứa zone id")
        raw_host = raw_host.removesuffix(".")
        if ":" in raw_host:
            host = raw_host
        else:
            try:
                host = idna.encode(raw_host, uts46=True).decode("ascii")
            except (UnicodeError, idna.IDNAError) as exc:
                raise UnsafeUrlError("HOST_NOT_ALLOWED", "Host không hợp lệ") from exc
        path = parts.path or "/"

        literal = _parse_ip_literal(host)
        if literal is not None:
            if not _is_allowed_ip(literal):
                raise UnsafeUrlError("HOST_NOT_ALLOWED", f"Host không được phép: {host}")
            canonical = str(literal)
            return _Target(parts.scheme, canonical, canonical, port, path, parts.query)

        try:
            resolved = await self._resolver(host, port)
        except OSError as exc:
            raise UnsafeUrlError("DNS_FAILED", f"Không phân giải được host: {host}") from exc
        if not resolved:
            raise UnsafeUrlError("DNS_FAILED", f"Không phân giải được host: {host}")
        ips: list[_IP] = []
        for raw in resolved:
            try:
                ips.append(ipaddress.ip_address(raw))
            except ValueError as exc:
                raise UnsafeUrlError(
                    "HOST_NOT_ALLOWED", f"Địa chỉ không hợp lệ cho {host}"
                ) from exc
        if not all(_is_allowed_ip(ip) for ip in ips):
            raise UnsafeUrlError(
                "HOST_NOT_ALLOWED", f"Host phân giải về địa chỉ không công khai: {host}"
            )
        return _Target(parts.scheme, host, str(ips[0]), port, path, parts.query)

    async def get(self, url: str) -> SafeResponse:
        try:
            async with asyncio.timeout(self._total_timeout_s):
                return await self._get(url)
        except TimeoutError as exc:
            raise FetchError(f"Quá thời gian tổng khi tải {_safe_label(url)}") from exc

    async def _get(self, url: str) -> SafeResponse:
        current = urldefrag(url)[0]
        for hop in range(self._max_redirects + 1):
            target = await self._validate(current)
            result = await self._request(target)
            location = result.headers.get("location")
            if result.status_code in _REDIRECT_STATUSES and location:
                if hop >= self._max_redirects:
                    raise UnsafeUrlError("TOO_MANY_REDIRECTS", "Quá nhiều lần chuyển hướng")
                current = urldefrag(urljoin(current, location))[0]
                continue
            return SafeResponse(
                status_code=result.status_code,
                final_url=current,
                headers=result.headers,
                body=result.body,
                truncated=result.truncated,
            )
        raise UnsafeUrlError("TOO_MANY_REDIRECTS", "Quá nhiều lần chuyển hướng")  # pragma: no cover

    async def _read_body(self, resp: httpx.Response) -> tuple[bytes, bool]:
        limit = self._max_bytes
        decoder = _make_decoder(resp.headers)
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in resp.aiter_raw():
                if decoder is None:
                    room = limit - size
                    if len(chunk) > room:
                        chunks.append(chunk[:room])
                        return b"".join(chunks), True
                    chunks.append(chunk)
                    size += len(chunk)
                    continue
                data = chunk
                while data:
                    room = limit - size
                    if room <= 0:
                        return b"".join(chunks), True
                    out = decoder.decompress(data, room)
                    chunks.append(out)
                    size += len(out)
                    data = decoder.unconsumed_tail
                    if decoder.eof:
                        return b"".join(chunks), False
                if size >= limit and not decoder.eof:
                    return b"".join(chunks), True
        except zlib.error as exc:
            raise FetchFailedError("Nội dung nén không hợp lệ") from exc
        return b"".join(chunks), False

    async def _request(self, target: _Target) -> _Hop:
        host_header = _fmt_host(target.host)
        if target.port != _DEFAULT_PORTS[target.scheme]:
            host_header = f"{host_header}:{target.port}"
        url = f"{target.scheme}://{_fmt_host(target.ip)}:{target.port}{target.path}"
        if target.query:
            url = f"{url}?{target.query}"
        headers = {
            "Host": host_header,
            "User-Agent": _USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        extensions: dict[str, object] = {}
        if target.scheme == "https":
            extensions["sni_hostname"] = target.host
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._timeout_s,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                _bare(client).stream("GET", url, headers=headers, extensions=extensions) as resp,
            ):
                body, truncated = b"", False
                if not (resp.status_code in _REDIRECT_STATUSES and resp.headers.get("location")):
                    body, truncated = await self._read_body(resp)
                return _Hop(resp.status_code, dict(resp.headers), body, truncated)
        except httpx.InvalidURL as exc:
            raise UnsafeUrlError("INVALID_URL", "URL không hợp lệ") from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise FetchError(f"Lỗi mạng hoặc timeout khi tải {target.label}") from exc
        except httpx.HTTPError as exc:
            raise FetchFailedError(f"Lỗi HTTP khi tải {target.label}") from exc
