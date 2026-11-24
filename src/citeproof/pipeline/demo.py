"""Lớp bọc thử nghiệm quanh stage thật cho môi trường local (QĐ-10, spec web UI §7.11).

Đọc `config.demo` của lần chạy: chờ `step_delay_s` giây (tối đa 5) rồi chạy stage bên
trong, hoặc ném `InputError` ở bước `fail_at` mà không chạy stage bên trong, để thử
được trang tiến độ, lỗi và "Tiếp tục từ bước N" trên pipeline thật. `build_registry`
chỉ bọc khi `settings.env == "local"`.
"""

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.errors import InputError
from citeproof.db.models import VerificationRun
from citeproof.pipeline.stages import PipelineStage, StageContext, StageResult

MAX_DELAY_S = 5.0
Sleep = Callable[[float], Awaitable[None]]


class DemoStage:
    def __init__(
        self,
        inner: PipelineStage,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self._session_factory = session_factory
        self._sleep = sleep

    async def run(self, ctx: StageContext) -> StageResult:
        async with self._session_factory() as session:
            config = await session.scalar(
                select(VerificationRun.config).where(VerificationRun.id == ctx.run_id)
            )
        demo = (config or {}).get("demo")
        if isinstance(demo, dict):
            try:
                delay = min(max(float(demo.get("step_delay_s") or 0), 0.0), MAX_DELAY_S)
            except (TypeError, ValueError):
                delay = 0.0
            if delay > 0:
                await self._sleep(delay)
            if demo.get("fail_at") == self.name.value:
                raise InputError(f"Lỗi mô phỏng ở bước {self.name.value[1:]}")
        return await self.inner.run(ctx)
