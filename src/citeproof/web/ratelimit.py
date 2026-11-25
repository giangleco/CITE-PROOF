"""Giới hạn số yêu cầu mỗi phút theo IP, trong tiến trình (T-608).

Cửa sổ cố định 60 giây, khóa `(ip, bucket)`. Trạng thái nằm trong bộ nhớ nên chỉ đúng khi
chạy một process app. Middleware ASGI thuần để không chặn luồng upload; không đọc body.
"""

import math
import time
from collections.abc import Callable

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

WINDOW_SECONDS = 60.0
EXEMPT_PREFIXES = ("/static/", "/api/v1/health")


def _bucket(method: str, path: str) -> str:
    if method == "POST" and (path.endswith("/upload") or path == "/documents"):
        return "upload"
    return "all"


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        per_minute: int,
        uploads_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.limits = {"all": per_minute, "upload": uploads_per_minute}
        self.clock = clock
        # (ip, bucket) -> (đầu cửa sổ, số yêu cầu)
        self.windows: dict[tuple[str, str], tuple[float, int]] = {}

    def _prune(self, now: float) -> None:
        expired = [k for k, (start, _) in self.windows.items() if now - start >= WINDOW_SECONDS]
        for key in expired:
            del self.windows[key]

    def _hit(self, ip: str, bucket: str) -> int | None:
        """Ghi một yêu cầu; trả về số giây chờ nếu vượt giới hạn, ngược lại None."""
        now = self.clock()
        self._prune(now)
        key = (ip, bucket)
        start, count = self.windows.get(key, (now, 0))
        if count >= self.limits[bucket]:
            return max(1, math.ceil(start + WINDOW_SECONDS - now))
        self.windows[key] = (start, count + 1)
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path.startswith(EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        retry_after = self._hit(ip, _bucket(scope["method"], path))
        if retry_after is None:
            await self.app(scope, receive, send)
            return
        from citeproof.web import error_response

        response = error_response(Request(scope, receive), 429)
        response.headers["Retry-After"] = str(retry_after)

        async def no_body() -> Message:
            return {"type": "http.disconnect"}

        await response(scope, no_body, send)
