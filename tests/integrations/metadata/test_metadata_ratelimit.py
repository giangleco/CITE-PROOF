"""RateLimiter theo nguồn với đồng hồ giả (T-304).

Tên tệp khác `test_ratelimit.py` của giới hạn tần suất web (Task 22), preflight F18.
"""

import asyncio

import pytest

from citeproof.integrations.metadata import RateLimiter


class FakeClock:
    """Đồng hồ giả: `sleep` ghi lại thời gian chờ và đẩy đồng hồ tới trước."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_rate_limiter_spacing() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock, sleep=clock.sleep)
    waits: list[float] = []
    for _ in range(3):
        before = sum(clock.sleeps)
        await limiter.acquire()
        waits.append(sum(clock.sleeps) - before)
    assert waits == [0.0, 0.5, 0.5]
    assert limiter.rate_per_second == 2.0


async def test_rate_limiter_no_wait_after_idle() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock, sleep=clock.sleep)
    await limiter.acquire()
    clock.now += 5.0
    await limiter.acquire()
    assert clock.sleeps == []
    await limiter.acquire()
    assert clock.sleeps == [0.5]


async def test_rate_limiter_spaces_concurrent_callers() -> None:
    clock = FakeClock()
    sleeps: list[float] = []

    async def no_advance_sleep(seconds: float) -> None:
        # Các coroutine cùng chờ: đồng hồ chỉ đứng yên, mỗi người nhận một khe riêng.
        sleeps.append(seconds)

    limiter = RateLimiter(4.0, clock=clock, sleep=no_advance_sleep)
    await asyncio.gather(*(limiter.acquire() for _ in range(4)))
    assert sorted(sleeps) == [0.25, 0.5, 0.75]


@pytest.mark.parametrize("rate", [0.0, -1.0, float("nan"), float("inf")])
def test_rate_limiter_rejects_invalid_rate(rate: float) -> None:
    with pytest.raises(ValueError):
        RateLimiter(rate)
