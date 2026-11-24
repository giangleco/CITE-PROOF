"""Giới hạn tốc độ gọi API theo nguồn (spec §7.3, T-304).

Mỗi lần `acquire` giữ một khe thời gian cách khe trước `1 / rate` giây rồi chờ tới khe đó.
Không dùng khóa asyncio: đọc và ghi `_next` không có `await` xen giữa nên an toàn giữa các
coroutine, và một limiter dùng được qua nhiều event loop (chia sẻ trong cả tiến trình).
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        rate_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not math.isfinite(rate_per_second) or rate_per_second <= 0:
            raise ValueError("rate_per_second phải là số dương hữu hạn")
        self._rate = rate_per_second
        self._interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None

    @property
    def rate_per_second(self) -> float:
        return self._rate

    async def acquire(self) -> None:
        """Chờ tới khe kế tiếp; lần gọi sau một khoảng nghỉ đủ dài không phải chờ."""
        now = self._clock()
        start = now if self._next is None else max(now, self._next)
        self._next = start + self._interval
        wait = start - now
        if wait > 0:
            await self._sleep(wait)
