"""Tài liệu web: URL còn hoạt động không, có bản lưu trữ archive.org không (FR-15, T-303).

URL lấy từ bài của người dùng, nên chỉ tải qua `SafeHttpClient` (chặn SSRF: IP nội bộ, địa
chỉ metadata đám mây, scheme lạ). Không bao giờ ghi log URL hay câu trả lời. Tra archive.org
chỉ chạy khi `web_archive_lookup` bật (QĐ-42).
"""

import json
from urllib.parse import urlencode, urlsplit

from citeproof.integrations.http_safe import (
    FetchError,
    FetchFailedError,
    SafeHttpClient,
    UnsafeUrlError,
)

ARCHIVE_API = "https://archive.org/wayback/available"
_ARCHIVE_HOST = "web.archive.org"
_FETCH_ERRORS = (UnsafeUrlError, FetchError, FetchFailedError)


async def check_url(safe_http: SafeHttpClient, url: str) -> bool | None:
    """2xx/3xx → True; 403 và 429 → None; 4xx/5xx khác → False.

    403 (chặn bot) và 429 (giới hạn tốc độ) không chứng minh URL hỏng nên là "chưa rõ" (None),
    như URL không an toàn hoặc lỗi mạng.
    """
    try:
        response = await safe_http.get(url)
    except _FETCH_ERRORS:
        return None
    status = response.status_code
    if status in (403, 429):
        return None
    return 200 <= status < 400


async def archive_lookup(safe_http: SafeHttpClient, url: str) -> str | None:
    """URL bản lưu gần nhất trên web.archive.org; không có hoặc tra lỗi → None."""
    try:
        response = await safe_http.get(f"{ARCHIVE_API}?{urlencode({'url': url})}")
    except _FETCH_ERRORS:
        return None
    if response.status_code != 200 or response.truncated:
        return None
    try:
        data = json.loads(response.body)
    except ValueError:
        return None
    snapshots = data.get("archived_snapshots") if isinstance(data, dict) else None
    closest = snapshots.get("closest") if isinstance(snapshots, dict) else None
    if not isinstance(closest, dict) or closest.get("available") is not True:
        return None
    snapshot = closest.get("url")
    if not isinstance(snapshot, str):
        return None
    try:
        parts = urlsplit(snapshot)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.hostname != _ARCHIVE_HOST:
        return None
    return snapshot
