"""Hạn mức chi phí của S8 có giữ chỗ cho cặp đang chạy (QĐ-32; Task 13)."""

import asyncio
from decimal import Decimal

import pytest

from citeproof.core import defaults
from citeproof.modules.verification.stage import _CostGate, _PairBudget, judge_reserve
from citeproof.pipeline.config import RunConfig


class _Cost:
    """`run_cost` giả: giá trị đặt tay, không đọc DB."""

    def __init__(self, value: str) -> None:
        self.value = Decimal(value)

    async def __call__(self) -> Decimal:
        return self.value


async def _first_done(tasks: set[asyncio.Task[bool]]) -> asyncio.Task[bool]:
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    [task] = done
    return task


async def test_reservation_admits_one_pair_until_release() -> None:
    cost = _Cost("0.50")
    gate = _CostGate(cost, Decimal("1.00"))
    reserve = Decimal("0.30")
    tasks = {asyncio.create_task(gate.allow(reserve)) for _ in range(3)}

    first = await _first_done(tasks)
    assert first.result() is True
    pending = tasks - {first}
    # 0.50 + 0.30 (đang giữ) + 0.30 >= 1.00: hai cặp còn lại không thể được phép trước release.
    assert not any(t.done() for t in pending)
    assert gate.reserved == reserve

    cost.value = Decimal("0.60")  # cặp đầu tiêu thật 0.10
    await gate.release(reserve)
    second = await _first_done(pending)
    assert second.result() is True
    [third] = pending - {second}
    assert not third.done()  # 0.60 + 0.30 + 0.30 >= 1.00
    assert gate.reached is False

    cost.value = Decimal("0.75")  # 0.75 + 0.30 >= 1.00: không còn đủ cho một cặp
    await gate.release(reserve)
    assert await third is False
    assert gate.reached is True
    assert gate.closed is True and gate.stopped is False
    assert gate.reserved == 0
    assert await gate.allow(reserve) is False  # không nhận cặp mới nữa
    cost.value = Decimal("1.00")
    assert await gate.allow(Decimal(0)) is False  # chi phí thật chạm hạn mức: dừng hẳn
    assert gate.stopped is True


async def test_zero_reserve_keeps_old_semantics() -> None:
    cost = _Cost("0.99")
    gate = _CostGate(cost, Decimal("1.00"))
    assert await gate.allow(Decimal("0.005")) is True
    # Lời gọi tiếp theo của cặp đang giữ chỗ (reserve 0) không chờ chỗ của chính nó.
    assert await gate.allow(Decimal(0)) is True
    cost.value = Decimal("1.00")
    assert await gate.allow(Decimal(0)) is False
    assert gate.reached is True


async def test_new_pair_denial_does_not_stop_holder_follow_up() -> None:
    """A giữ chỗ ở 4.80; chi phí thành 4.85; B bị từ chối (4.85 + 0.30 >= 5) nhưng lời gọi
    tiếp theo của A (đã trả tiền giữ chỗ) vẫn được phép vì chi phí thật 4.85 < 5."""
    cost = _Cost("4.80")
    gate = _CostGate(cost, Decimal("5"))
    reserve = Decimal("0.10")
    a = _PairBudget(gate, reserve)
    b = _PairBudget(gate, Decimal("0.30"))
    assert await a() is True
    cost.value = Decimal("4.85")
    assert await b() is False
    assert gate.reached is True  # cảnh báo COST_LIMIT_REACHED vẫn phát
    assert gate.stopped is False
    assert await a() is True
    await a.release()
    assert gate.reserved == 0


class _FakeGate:
    def __init__(self, answers: list[bool]) -> None:
        self.answers = answers
        self.allowed: list[Decimal] = []
        self.released: list[Decimal] = []

    async def allow(self, reserve: Decimal) -> bool:
        self.allowed.append(reserve)
        return self.answers.pop(0)

    async def release(self, reserve: Decimal) -> None:
        self.released.append(reserve)


async def test_pair_budget_reserves_once_and_releases_once() -> None:
    gate = _FakeGate([True, True, True])
    budget = _PairBudget(gate, Decimal("0.16"))
    assert await budget() is True
    assert await budget() is True
    assert await budget() is True
    assert gate.allowed == [Decimal("0.16"), Decimal(0), Decimal(0)]
    await budget.release()
    await budget.release()
    assert gate.released == [Decimal("0.16")]


async def test_pair_budget_denied_holds_nothing() -> None:
    gate = _FakeGate([False])
    budget = _PairBudget(gate, Decimal("0.16"))
    assert await budget() is False
    await budget.release()
    assert gate.released == []


def test_judge_reserve_uses_output_price_and_max_tokens() -> None:
    config = RunConfig()
    reserve = judge_reserve(config, defaults.LLM_PRICES)
    model = config.models["verdict.judge"]
    out_price = Decimal(str(defaults.LLM_PRICES[model][1]))
    expected = out_price * config.max_tokens["verdict.judge"] / Decimal(1_000_000)
    assert reserve == expected
    assert reserve == Decimal("0.16")


@pytest.mark.parametrize("prices", [{}, {"other-model": (1.0, 2.0)}])
def test_judge_reserve_unpriced_model_is_zero(prices: dict[str, tuple[float, float]]) -> None:
    assert judge_reserve(RunConfig(), prices) == 0
