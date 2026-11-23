"""Giai đoạn thử cho môi trường local (spec web UI §7.11).

Đọc `config.demo` của lần chạy: chờ `step_delay_s` giây (tối đa 5) và ném `InputError`
ở bước `fail_at`, để thử được trang tiến độ, lỗi và "Tiếp tục từ bước N". Chỉ
`python -m citeproof.pipeline` ở môi trường local dùng registry này.
"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import StageName
from citeproof.core.errors import InputError
from citeproof.db.models import VerificationRun
from citeproof.pipeline.stages import StageRegistry, StageResult

MAX_DELAY_S = 5.0
Sleep = Callable[[float], Awaitable[None]]


class DemoStage:
    def __init__(
        self,
        name: StageName,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.name = name
        self._session_factory = session_factory
        self._sleep = sleep

    async def run(self, run_id: UUID) -> StageResult:
        async with self._session_factory() as session:
            config = await session.scalar(
                select(VerificationRun.config).where(VerificationRun.id == run_id)
            )
        demo = (config or {}).get("demo")
        if not isinstance(demo, dict):
            return StageResult()
        try:
            delay = min(max(float(demo.get("step_delay_s") or 0), 0.0), MAX_DELAY_S)
        except (TypeError, ValueError):
            delay = 0.0
        if delay > 0:
            await self._sleep(delay)
        if demo.get("fail_at") == self.name.value:
            raise InputError(f"Lỗi mô phỏng ở bước {self.name.value[1:]}")
        return StageResult()


def demo_registry(session_factory: async_sessionmaker[AsyncSession]) -> StageRegistry:
    return StageRegistry(DemoStage(name, session_factory) for name in StageName)
