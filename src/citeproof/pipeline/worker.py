"""Worker: nhận job, chạy stage ngoài giao dịch, ghi kết quả trong giao dịch mới.

Log chỉ gồm các trường trong allowlist; không bao giờ ghi thông điệp lỗi (có thể
chứa nội dung tài liệu), chỉ ghi tên kiểu lỗi.
"""

import asyncio
import contextlib
import logging
import time
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.errors import CiteProofError, InternalError
from citeproof.core.logging import log_context
from citeproof.core.settings import Settings
from citeproof.pipeline.jobs import claim_next
from citeproof.pipeline.runs import complete_stage, fail_expired_jobs, fail_stage
from citeproof.pipeline.stages import StageRegistry

logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _failure_level(error: Exception) -> int:
    expected = isinstance(error, CiteProofError) and not isinstance(error, InternalError)
    return logging.WARNING if expected else logging.ERROR


async def work_once(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    worker_id: str,
    settings: Settings,
) -> bool:
    """Xử lý tối đa một job; trả về False khi không có job nào nhận được."""
    lock_timeout = timedelta(seconds=settings.job_lock_timeout_seconds)
    async with session_factory() as session:
        expired = await fail_expired_jobs(
            session, lock_timeout=lock_timeout, max_attempts=settings.job_max_attempts
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

    async with session_factory() as session:
        job = await claim_next(
            session, worker_id, lock_timeout, max_attempts=settings.job_max_attempts
        )
        await session.commit()
    if job is None:
        return False

    with log_context(
        run_id=str(job.run_id), stage=job.stage.value, job_id=str(job.id), worker_id=worker_id
    ):
        started = time.monotonic()
        logger.info("stage_started")
        try:
            result = await registry.get(job.stage).run(job.run_id)
        except Exception as exc:
            logger.log(
                _failure_level(exc),
                "stage_failed",
                extra={"error_type": type(exc).__name__, "duration_ms": _elapsed_ms(started)},
            )
            async with session_factory() as session:
                await fail_stage(session, job, exc, settings)
                await session.commit()
            return True
        logger.info("stage_completed", extra={"duration_ms": _elapsed_ms(started)})
        async with session_factory() as session:
            await complete_stage(session, job, result)
            await session.commit()
    return True


async def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    worker_id: str,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    """Lặp `work_once` đến khi `stop` được đặt; nghỉ một nhịp poll khi không có việc."""
    with log_context(worker_id=worker_id):
        logger.info("worker_started")
        while not stop.is_set():
            if await work_once(session_factory, registry, worker_id, settings):
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
        logger.info("worker_stopped")
