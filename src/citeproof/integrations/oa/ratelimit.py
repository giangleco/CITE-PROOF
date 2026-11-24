"""Giới hạn tốc độ theo host khi tìm và tải bản mở ở S5.

arXiv yêu cầu client tự động không quá một yêu cầu mỗi 3 giây; ACL Anthology và Unpaywall
được giới hạn 1 yêu cầu mỗi giây. Các host của cùng một dịch vụ dùng chung một `RateLimiter`
(của `integrations.metadata.ratelimit`). Host không có trong bảng không bị giới hạn.

`HostRateLimits` dựng một lần cho mỗi tiến trình (`PipelineDeps.source_limits`) và dùng chung
cho mọi lần chạy và mọi work S5 xử lý song song. Giới hạn xét host của URL ban đầu; các lần
chuyển hướng trong cùng một lần tải không tính thêm.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from urllib.parse import urlsplit

from citeproof.integrations.metadata.ratelimit import RateLimiter

# Dịch vụ → (số yêu cầu mỗi giây, các host của dịch vụ).
OA_HOST_RATES: Mapping[str, tuple[float, frozenset[str]]] = MappingProxyType(
    {
        "arxiv": (1 / 3, frozenset({"export.arxiv.org", "arxiv.org", "www.arxiv.org"})),
        "acl_anthology": (1.0, frozenset({"aclanthology.org"})),
        "unpaywall": (1.0, frozenset({"api.unpaywall.org"})),
    }
)


class HostRateLimits:
    def __init__(
        self,
        rates: Mapping[str, tuple[float, frozenset[str]]] = OA_HOST_RATES,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._by_host: dict[str, RateLimiter] = {}
        for rate, hosts in rates.values():
            limiter = RateLimiter(rate, clock=clock, sleep=sleep)
            for host in hosts:
                self._by_host[host] = limiter

    def limiter_for(self, url: str) -> RateLimiter | None:
        """Limiter của host trong `url`; host không giới hạn hoặc URL hỏng → None."""
        try:
            host = urlsplit(url).hostname
        except ValueError:
            return None
        if not host:
            return None
        return self._by_host.get(host.rstrip("."))

    async def acquire(self, url: str) -> None:
        """Chờ tới khe kế tiếp của host trong `url` (không chờ nếu host không bị giới hạn)."""
        limiter = self.limiter_for(url)
        if limiter is not None:
            await limiter.acquire()
