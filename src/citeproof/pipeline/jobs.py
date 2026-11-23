"""Hàng đợi job trên PostgreSQL (claim bằng FOR UPDATE SKIP LOCKED).

Mọi so sánh thời gian dùng đồng hồ của database (`now()`), để worker trên nhiều
máy luôn thống nhất. Các hàm không commit: caller quyết định ranh giới giao dịch.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import JobStatus, StageName
from citeproof.db.models.jobs import Job

_MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    run_id: uuid.UUID
    stage: StageName
    attempts: int
    payload: dict[str, Any]


def backoff_delay(attempts: int, base_seconds: float) -> timedelta:
    return timedelta(seconds=base_seconds * 2 ** (attempts - 1))


async def enqueue(
    session: AsyncSession,
    run_id: uuid.UUID,
    stage: StageName,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
) -> bool:
    """Thêm job; trả về False nếu (run_id, stage) đã tồn tại."""
    values: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage.value,
        "payload": payload or {},
    }
    if run_after is not None:
        values["run_after"] = run_after
    stmt = (
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["run_id", "stage"])
        .returning(Job.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def claim_next(
    session: AsyncSession, worker_id: str, lock_timeout: timedelta
) -> ClaimedJob | None:
    """Nhận một job sẵn sàng (hoặc job RUNNING đã quá hạn khóa); caller commit."""
    candidate = (
        select(Job.id)
        .where(
            or_(
                and_(Job.status == JobStatus.QUEUED.value, Job.run_after <= func.now()),
                and_(
                    Job.status == JobStatus.RUNNING.value,
                    Job.locked_at < func.now() - lock_timeout,
                ),
            )
        )
        .order_by(Job.run_after)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == candidate)
        .values(
            status=JobStatus.RUNNING.value,
            attempts=Job.attempts + 1,
            locked_at=func.now(),
            locked_by=worker_id,
        )
        .returning(Job.id, Job.run_id, Job.stage, Job.attempts, Job.payload)
        .execution_options(synchronize_session=False)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    return ClaimedJob(
        id=row.id,
        run_id=row.run_id,
        stage=StageName(row.stage),
        attempts=row.attempts,
        payload=row.payload or {},
    )


async def mark_done(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.DONE.value, locked_at=None, locked_by=None)
        .execution_options(synchronize_session=False)
    )


async def mark_failed(
    session: AsyncSession,
    job_id: uuid.UUID,
    error: BaseException,
    *,
    retryable: bool,
    max_attempts: int,
    backoff_base_seconds: float,
) -> JobStatus:
    """Đặt lại lịch (QUEUED + backoff) nếu còn lượt thử, ngược lại FAILED."""
    attempts = (await session.execute(select(Job.attempts).where(Job.id == job_id))).scalar_one()
    last_error = f"{type(error).__name__}: {error}"[:_MAX_ERROR_LENGTH]
    if retryable and attempts < max_attempts:
        status = JobStatus.QUEUED
        run_after = func.now() + backoff_delay(attempts, backoff_base_seconds)
        values: dict[str, Any] = {"run_after": run_after}
    else:
        status = JobStatus.FAILED
        values = {}
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=status.value,
            last_error=last_error,
            locked_at=None,
            locked_by=None,
            **values,
        )
        .execution_options(synchronize_session=False)
    )
    return status
