"""HTTP client chống SSRF (docs/07a §4.9, rủi ro SR-11).

Mọi URL suy ra từ dữ liệu người dùng chỉ được tải qua client này.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, urldefrag, urljoin, urlsplit

import httpx

from citeproof.core.errors import InputError, RetryableError

UnsafeUrlCode = Literal[
    "SCHEME_NOT_ALLOWED",
    "HOST_NOT_ALLOWED",
    "DNS_FAILED",
    "TOO_MANY_REDIRECTS",
    "CREDENTIALS_NOT_ALLOWED",
]

Resolver = Callable[[str, int], Awaitable[list[str]]]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LEGACY_IPV4_RE = re.compile(r"^[0-9a-fA-FxX.]+$")
_USER_AGENT = "CiteProof/0.1"


class UnsafeUrlError(InputError):
    """URL bị từ chối vì không an toàn."""

    def __init__(self, code: UnsafeUrlCode, message: str) -> None:
        super().__init__(message)
        self.code: UnsafeUrlCode = code


class FetchError(RetryableError):
    """Timeout hoặc lỗi kết nối (thông điệp không chứa query string)."""


@dataclass(frozen=True)
class SafeResponse:
    status_code: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes
    truncated: bool


async def _default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _is_allowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_allowed_ip(ip.ipv4_mapped)
        embedded: list[ipaddress.IPv4Address] = []
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if not all(_is_allowed_ip(e) for e in embedded):
            return False
    return ip.is_global and not ip.is_multicast


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    if _LEGACY_IPV4_RE.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _log_safe(parts: SplitResult) -> str:
    return f"{parts.scheme}://{parts.hostname or '?'}"


class SafeHttpClient:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 10.0,
        max_bytes: int = 5_000_000,
        max_redirects: int = 5,
    ) -> None:
        self._resolver = resolver or _default_resolver
        self._transport = transport
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def _validate(self, url: str) -> tuple[SplitResult, str, int]:
        """Trả về (parts, ip đã kiểm tra, port)."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise UnsafeUrlError("HOST_NOT_ALLOWED", "URL không hợp lệ") from exc
        if parts.scheme not in _DEFAULT_PORTS:
            raise UnsafeUrlError("SCHEME_NOT_ALLOWED", f"Scheme không được phép: {parts.scheme!r}")
        if "@" in parts.netloc:
            raise UnsafeUrlError("CREDENTIALS_NOT_ALLOWED", "URL không được chứa userinfo")
        host = parts.hostname
        if not host:
            raise UnsafeUrlError("HOST_NOT_ALLOWED", "URL thiếu host")
        port = port or _DEFAULT_PORTS[parts.scheme]

        literal = _parse_ip_literal(host)
        if literal is not None:
            if not _is_allowed_ip(literal):
                raise UnsafeUrlError("HOST_NOT_ALLOWED", f"Host không được phép: {host}")
            return parts, str(literal), port

        try:
            resolved = await self._resolver(host, port)
        except OSError as exc:
            raise UnsafeUrlError("DNS_FAILED", f"Không phân giải được host: {host}") from exc
        if not resolved:
            raise UnsafeUrlError("DNS_FAILED", f"Không phân giải được host: {host}")
        ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
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
        return parts, str(ips[0]), port

    async def get(self, url: str) -> SafeResponse:
        current = urldefrag(url)[0]
        for hop in range(self._max_redirects + 1):
            parts, ip, port = await self._validate(current)
            response = await self._request(parts, ip, port)
            location = response.headers.get("location")
            if response.status_code in _REDIRECT_STATUSES and location:
                if hop >= self._max_redirects:
                    raise UnsafeUrlError("TOO_MANY_REDIRECTS", "Quá nhiều lần chuyển hướng")
                current = urldefrag(urljoin(current, location))[0]
                continue
            return SafeResponse(
                status_code=response.status_code,
                final_url=current,
                headers=dict(response.headers),
                body=response.body,
                truncated=response.truncated,
            )
        raise UnsafeUrlError("TOO_MANY_REDIRECTS", "Quá nhiều lần chuyển hướng")  # pragma: no cover

    async def _request(self, parts: SplitResult, ip: str, port: int) -> SafeResponse:
        hostname = parts.hostname or ""
        ip_host = f"[{ip}]" if ":" in ip else ip
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if port != _DEFAULT_PORTS[parts.scheme]:
            host_header = f"{host_header}:{port}"
        path = parts.path or "/"
        target = f"{parts.scheme}://{ip_host}:{port}{path}"
        if parts.query:
            target = f"{target}?{parts.query}"
        headers = {"Host": host_header, "User-Agent": _USER_AGENT, "Accept": "*/*"}
        extensions: dict[str, object] = {}
        if parts.scheme == "https":
            extensions["sni_hostname"] = hostname
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._timeout_s,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream("GET", target, headers=headers, extensions=extensions) as resp,
            ):
                chunks: list[bytes] = []
                size = 0
                truncated = False
                if resp.status_code not in _REDIRECT_STATUSES:
                    async for chunk in resp.aiter_bytes():
                        room = self._max_bytes - size
                        if len(chunk) > room:
                            chunks.append(chunk[:room])
                            truncated = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                return SafeResponse(
                    status_code=resp.status_code,
                    final_url="",
                    headers=dict(resp.headers),
                    body=b"".join(chunks),
                    truncated=truncated,
                )
        except httpx.TimeoutException as exc:
            raise FetchError(f"Timeout khi tải {_log_safe(parts)}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Lỗi kết nối khi tải {_log_safe(parts)}") from exc
