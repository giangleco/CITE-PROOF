"""Xử lý theo từng mục trong một stage (07a §4.3.2).

Bỏ qua mục đã có kết quả (tiếp tục sau lỗi), giới hạn song song bằng semaphore và
gom lỗi theo mục thay vì dừng cả stage.
"""

import asyncio
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class UnitReport:
    processed: int
    skipped: int
    failed: tuple[tuple[str, BaseException], ...]


async def process_units[U](
    units: Sequence[U],
    *,
    key: Callable[[U], str],
    done_keys: Collection[str],
    handle: Callable[[U], Awaitable[None]],
    concurrency: int,
) -> UnitReport:
    """Chạy `handle` cho các mục chưa xong; `failed` giữ thứ tự của `units`.

    Chỉ `Exception` được gom; hủy (`asyncio.CancelledError`) vẫn lan ra ngoài.
    """
    if concurrency < 1:
        raise ValueError("concurrency phải >= 1")
    keyed = [(key(unit), unit) for unit in units]
    pending = [(unit_key, unit) for unit_key, unit in keyed if unit_key not in done_keys]
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(unit: U) -> Exception | None:
        async with semaphore:
            try:
                await handle(unit)
            except Exception as exc:
                return exc
            return None

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(run_one(unit)) for _, unit in pending]
    outcomes = [task.result() for task in tasks]
    failed = tuple(
        (unit_key, error)
        for (unit_key, _), error in zip(pending, outcomes, strict=True)
        if error is not None
    )
    return UnitReport(
        processed=len(pending) - len(failed),
        skipped=len(units) - len(pending),
        failed=failed,
    )
