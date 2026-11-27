"""Worker: nhận job, chạy stage ngoài giao dịch, ghi kết quả trong giao dịch mới.

Trong lúc stage chạy, một vòng heartbeat song song gia hạn lease mỗi
`job_heartbeat_seconds`; lease mất thì stage bị hủy và kết quả bị bỏ. Log chỉ gồm
các trường trong allowlist; không bao giờ ghi thông điệp lỗi (có thể chứa nội dung
tài liệu), chỉ ghi tên kiểu lỗi.

Trước stage có thể gọi LLM (`LLM_STAGES`), worker so provider đã đóng băng vào lần chạy với
provider đang cấu hình (spec §5.4): khác nhau thì job lỗi `LLM_PROVIDER_CHANGED`, không thử
lại. Stage tất định (S1, S5, S6, S9) không bị chặn.
"""

import asyncio
import contextlib
import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import StageName
from citeproof.core.errors import CiteProofError, InternalError
from citeproof.core.logging import log_context
from citeproof.core.settings import Settings
from citeproof.db.models import VerificationRun
from citeproof.pipeline.config import RunConfig, ensure_same_provider
from citeproof.pipeline.jobs import ClaimedJob, claim_next, renew_lease
from citeproof.pipeline.runs import complete_stage, fail_expired_jobs, fail_stage
from citeproof.pipeline.stages import PipelineStage, StageContext, StageRegistry, StageResult

logger = logging.getLogger(__name__)

# Stage có thể gọi `deps.llm`/`deps.agents`: S2 (W1), S3 (A1), S4 (W2), S7 (W3), S8 (verifier).
LLM_STAGES: frozenset[StageName] = frozenset(
    {StageName.S2, StageName.S3, StageName.S4, StageName.S7, StageName.S8}
)


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _failure_level(error: Exception) -> int:
    expected = isinstance(error, CiteProofError) and not isinstance(error, InternalError)
    return logging.WARNING if expected else logging.ERROR


async def _check_provider(
    session_factory: async_sessionmaker[AsyncSession], job: ClaimedJob, settings: Settings
) -> None:
    """Chặn stage cần LLM khi provider đã đổi (`ConflictError` không thử lại)."""
    if job.stage not in LLM_STAGES:
        return
    async with session_factory() as session:
        raw = await session.scalar(
            select(VerificationRun.config).where(VerificationRun.id == job.run_id)
        )
    ensure_same_provider(RunConfig.load(raw), settings)


async def _keep_lease(
    session_factory: async_sessionmaker[AsyncSession], job: ClaimedJob, settings: Settings
) -> None:
    """Gia hạn lease mỗi `job_heartbeat_seconds`; chỉ trả về khi lease đã mất.

    Lỗi khi gia hạn (ví dụ DB mất kết nối) được ghi log rồi thử lại ở nhịp sau: lease
    còn hạn thêm ít nhất một nhịp, và mọi ghi kết quả vẫn được rào bằng `locked_at`.
    """
    lease = timedelta(seconds=settings.job_lock_timeout_seconds)
    while True:
        await asyncio.sleep(settings.job_heartbeat_seconds)
        try:
            async with session_factory() as session:
                renewed = await renew_lease(session, job, lease)
                await session.commit()
        except Exception as exc:
            logger.warning("lease_renewal_failed", extra={"error_type": type(exc).__name__})
            continue
        if not renewed:
            return


async def _run_with_lease(
    session_factory: async_sessionmaker[AsyncSession],
    stage: PipelineStage,
    ctx: StageContext,
    settings: Settings,
) -> StageResult | None:
    """Chạy stage song song với vòng heartbeat; trả về None nếu lease mất trước khi xong.

    Lease mất thì task stage bị hủy. Lỗi của stage được ném lại nguyên vẹn. Worker bị
    hủy thì hủy cả stage lẫn heartbeat.
    """
    stage_task = asyncio.create_task(stage.run(ctx))
    heartbeat = asyncio.create_task(_keep_lease(session_factory, ctx.lease, settings))
    try:
        done, _ = await asyncio.wait({stage_task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stage_task.cancel()  # không có tác dụng nếu stage đã xong
        heartbeat.cancel()
        await asyncio.wait({stage_task, heartbeat})
    if stage_task in done:
        return stage_task.result()
    return None


async def work_once(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    worker_id: str,
    settings: Settings,
) -> bool:
    """Xử lý tối đa một job; trả về False khi không có job nào nhận được."""
    async with session_factory() as session:
        expired = await fail_expired_jobs(
            session,
            max_attempts=settings.job_max_attempts,
            lock_timeout=timedelta(seconds=settings.job_lock_timeout_seconds),
        )
        await session.commit()
    for stale in expired:
        logger.warning(
            "job_lease_expired",
            extra={
                "run_id": str(stale.run_id),
                "stage": stale.stage.value,
                "job_id": str(stale.id),
                "worker_id": worker_id,
                "error_type": "LeaseExpired",
            },
        )

    lease = timedelta(seconds=settings.job_lock_timeout_seconds)
    async with session_factory() as session:
        job = await claim_next(session, worker_id, lease, max_attempts=settings.job_max_attempts)
        await session.commit()
    if job is None:
        return False

    ctx = StageContext(run_id=job.run_id, lease=job, max_attempts=settings.job_max_attempts)
    with log_context(
        run_id=str(job.run_id), stage=job.stage.value, job_id=str(job.id), worker_id=worker_id
    ):
        started = time.monotonic()
        logger.info("stage_started")
        try:
            await _check_provider(session_factory, job, settings)
            result = await _run_with_lease(session_factory, registry.get(job.stage), ctx, settings)
        except Exception as exc:
            logger.log(
                _failure_level(exc),
                "stage_failed",
                extra={"error_type": type(exc).__name__, "duration_ms": _elapsed_ms(started)},
            )
            async with session_factory() as session:
                recorded = await fail_stage(session, job, exc, settings)
                await session.commit()
            if not recorded:
                logger.warning("stage_result_discarded")
            return True
        duration_ms = _elapsed_ms(started)
        if result is None:
            # Worker khác đã nhận lại job: không ghi gì, kể cả lỗi.
            logger.warning("stage_lease_lost", extra={"duration_ms": duration_ms})
            return True
        async with session_factory() as session:
            recorded = await complete_stage(session, job, result)
            await session.commit()
        if recorded:
            logger.info("stage_completed", extra={"duration_ms": duration_ms})
        else:
            logger.warning("stage_result_discarded", extra={"duration_ms": duration_ms})
    return True


async def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    worker_id: str,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    """Lặp `work_once` đến khi `stop` được đặt; nghỉ một nhịp poll khi không có việc.

    Lỗi của một vòng (ví dụ database mất kết nối) được ghi log rồi thử lại sau một
    nhịp poll, không làm dừng worker. Chỉ ghi tên kiểu lỗi: thông điệp lỗi của
    SQLAlchemy có thể chứa câu SQL.
    """
    with log_context(worker_id=worker_id):
        logger.info("worker_started")
        while not stop.is_set():
            try:
                if await work_once(session_factory, registry, worker_id, settings):
                    continue
            except Exception as exc:
                logger.error("worker_iteration_failed", extra={"error_type": type(exc).__name__})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
        logger.info("worker_stopped")
