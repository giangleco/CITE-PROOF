"""Helper cho test runner: chạy cạn hàng đợi và các stage giả lập."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import StageName
from citeproof.core.settings import Settings
from citeproof.pipeline.stages import StageRegistry, StageResult
from citeproof.pipeline.worker import work_once


async def drain(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    settings: Settings,
    worker_id: str = "w1",
) -> int:
    """Gọi `work_once` đến khi không còn job nhận được; trả về số job đã xử lý."""
    processed = 0
    while await work_once(session_factory, registry, worker_id, settings):
        processed += 1
    return processed


class CountingStage:
    """Stage ghi lại mỗi lần được gọi; trả về các cảnh báo cố định."""

    def __init__(self, name: StageName, warnings: Sequence[str] = ()) -> None:
        self.name = name
        self.warnings = tuple(warnings)
        self.calls: list[UUID] = []

    async def run(self, run_id: UUID) -> StageResult:
        self.calls.append(run_id)
        return StageResult(warnings=self.warnings)


class FailingStage(CountingStage):
    """Ném `error` ở `fail_times` lần gọi đầu, sau đó chạy bình thường."""

    def __init__(self, name: StageName, fail_times: int, error: BaseException) -> None:
        super().__init__(name)
        self.fail_times = fail_times
        self.error = error

    async def run(self, run_id: UUID) -> StageResult:
        result = await super().run(run_id)
        if len(self.calls) <= self.fail_times:
            raise self.error
        return result


def counting_registry(
    *overrides: CountingStage,
) -> tuple[StageRegistry, dict[StageName, CountingStage]]:
    """Registry gồm CountingStage cho mọi stage, thay bằng `overrides` theo tên."""
    stages = {name: CountingStage(name) for name in StageName}
    for stage in overrides:
        stages[stage.name] = stage
    return StageRegistry(stages.values()), stages


def pipeline_settings(*, job_max_attempts: int = 3) -> Settings:
    """Settings cố định cho test pipeline (không phụ thuộc biến môi trường)."""
    return Settings(
        job_max_attempts=job_max_attempts,
        job_backoff_base_seconds=5.0,
        job_lock_timeout_seconds=900,
        worker_poll_interval_seconds=0.01,
    )
