import asyncio

import pytest

from citeproof.pipeline.units import UnitReport, process_units


async def test_process_units_skips_done_and_collects_failures() -> None:
    error = RuntimeError("c hỏng")
    handled: list[str] = []

    async def handle(unit: str) -> None:
        if unit == "c":
            raise error
        handled.append(unit)

    report = await process_units(
        ["a", "b", "c", "d"], key=str, done_keys={"a"}, handle=handle, concurrency=2
    )
    assert report == UnitReport(processed=2, skipped=1, failed=(("c", error),))
    assert sorted(handled) == ["b", "d"]


async def test_process_units_respects_concurrency() -> None:
    active = 0
    peak = 0

    async def handle(unit: int) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    report = await process_units(
        list(range(10)), key=str, done_keys=(), handle=handle, concurrency=3
    )
    assert peak == 3
    assert report == UnitReport(processed=10, skipped=0, failed=())


async def test_process_units_reports_failures_in_unit_order() -> None:
    async def handle(unit: int) -> None:
        # Mục sau xong trước: thứ tự báo cáo vẫn theo thứ tự đầu vào.
        await asyncio.sleep(0.01 * (5 - unit))
        raise ValueError(str(unit))

    report = await process_units(
        list(range(5)), key=str, done_keys=(), handle=handle, concurrency=5
    )
    assert [key for key, _ in report.failed] == ["0", "1", "2", "3", "4"]
    assert report.processed == 0


async def test_process_units_rejects_non_positive_concurrency() -> None:
    async def handle(unit: str) -> None:
        return None

    with pytest.raises(ValueError):
        await process_units(["a"], key=str, done_keys=(), handle=handle, concurrency=0)


async def test_process_units_propagates_cancellation() -> None:
    async def handle(unit: str) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await process_units(["a"], key=str, done_keys=(), handle=handle, concurrency=1)
