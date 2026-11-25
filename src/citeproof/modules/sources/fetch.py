"""Tải một bản toàn văn mở qua `SafeHttpClient` (spec §7.5, SR-11).

URL bản mở lấy từ metadata bên thứ ba nên chỉ tải qua client chống SSRF (`PipelineDeps.source_http`,
giới hạn `max_source_bytes`). Chỉ nhận phản hồi 200, không bị cắt, thân bắt đầu bằng `%PDF-`;
mọi trường hợp khác (trang HTML, lỗi mạng, URL không an toàn) → None để S5 thử lựa chọn kế
tiếp. Log chỉ ghi nhà cung cấp và lý do, không bao giờ ghi URL.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Literal

from citeproof.integrations.http_safe import (
    FetchError,
    FetchFailedError,
    SafeHttpClient,
    UnsafeUrlError,
)
from citeproof.integrations.oa import AccessOption, HostRateLimits

logger = logging.getLogger(__name__)

PDF_SIGNATURE = b"%PDF-"
OPEN_SOURCE_PREFIX = "sources/open"
_FETCH_ERRORS = (UnsafeUrlError, FetchError, FetchFailedError)


@dataclass(frozen=True)
class FetchedFile:
    data: bytes
    url: str  # URL của lựa chọn đã tải (không phải URL sau chuyển hướng, có thể mang chữ ký)
    sha256: str


def open_source_key(sha256: str, ext: Literal["pdf", "abstract.json", "txt"]) -> str:
    """Khóa lưu trữ của nội dung mở dùng chung, đặt theo SHA-256 của nội dung."""
    return f"{OPEN_SOURCE_PREFIX}/{sha256}.{ext}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FullTextFetcher:
    def __init__(self, safe_http: SafeHttpClient, *, limits: HostRateLimits | None = None) -> None:
        self._safe_http = safe_http
        self._limits = limits

    async def fetch(self, option: AccessOption) -> FetchedFile | None:
        """Bản PDF của `option`, hoặc None nếu không tải được bản PDF đầy đủ.

        Với `limits`, chờ tới khe của host trước khi tải (arXiv 1 yêu cầu/3 s, ACL 1/s).
        """
        if self._limits is not None:
            await self._limits.acquire(option.url)
        try:
            response = await self._safe_http.get(option.url)
        except _FETCH_ERRORS as exc:
            logger.info(
                "source_fetch_failed provider=%s",
                option.provider,
                extra={"error_type": type(exc).__name__},
            )
            return None
        reason = _rejection(response.status_code, response.truncated, response.body)
        if reason is not None:
            logger.info("source_fetch_rejected provider=%s reason=%s", option.provider, reason)
            return None
        # Băm tới 30 MB: chạy ngoài event loop (hashlib nhả GIL với dữ liệu lớn).
        digest = await asyncio.to_thread(sha256_hex, response.body)
        return FetchedFile(data=response.body, url=option.url, sha256=digest)


def _rejection(status_code: int, truncated: bool, body: bytes) -> str | None:
    if status_code != 200:
        return f"http_{status_code}"
    if truncated:
        return "too_large"
    if not body.startswith(PDF_SIGNATURE):
        return "not_pdf"
    return None
