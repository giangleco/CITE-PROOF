"""Helper cho test runner: chạy cạn hàng đợi và các stage giả lập."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import StageName
from citeproof.core.settings import Settings
from citeproof.pipeline.jobs import ClaimedJob
from citeproof.pipeline.stages import StageContext, StageRegistry, StageResult
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


def stage_context(
    run_id: uuid.UUID,
    *,
    stage: StageName = StageName.S1,
    attempts: int = 1,
    max_attempts: int = 3,
) -> StageContext:
    """StageContext với lease giả (không có dòng job tương ứng), để gọi stage trực tiếp."""
    lease = ClaimedJob(
        id=uuid.uuid4(),
        run_id=run_id,
        stage=stage,
        attempts=attempts,
        payload={},
        locked_at=datetime.now(UTC),
    )
    return StageContext(run_id=run_id, lease=lease, max_attempts=max_attempts)


class CountingStage:
    """Stage ghi lại context của mỗi lần được gọi; trả về các cảnh báo cố định."""

    def __init__(self, name: StageName, warnings: Sequence[str] = ()) -> None:
        self.name = name
        self.warnings = tuple(warnings)
        self.calls: list[StageContext] = []

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls.append(ctx)
        return StageResult(warnings=self.warnings)


class FailingStage(CountingStage):
    """Ném `error` ở `fail_times` lần gọi đầu, sau đó chạy bình thường."""

    def __init__(self, name: StageName, fail_times: int, error: BaseException) -> None:
        super().__init__(name)
        self.fail_times = fail_times
        self.error = error

    async def run(self, ctx: StageContext) -> StageResult:
        result = await super().run(ctx)
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


def pipeline_settings(
    *,
    job_max_attempts: int = 3,
    job_lock_timeout_seconds: int = 900,
    job_heartbeat_seconds: float = 30.0,
) -> Settings:
    """Settings cố định cho test pipeline (không phụ thuộc biến môi trường)."""
    return Settings(
        job_max_attempts=job_max_attempts,
        job_backoff_base_seconds=5.0,
        job_lock_timeout_seconds=job_lock_timeout_seconds,
        job_heartbeat_seconds=job_heartbeat_seconds,
        worker_poll_interval_seconds=0.01,
    )
