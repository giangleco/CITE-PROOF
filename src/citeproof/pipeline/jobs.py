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
from citeproof.core.errors import CiteProofError
from citeproof.db.models.jobs import Job

_MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    run_id: uuid.UUID
    stage: StageName
    attempts: int
    payload: dict[str, Any]


def describe_error(error: BaseException) -> str:
    """Mô tả lỗi an toàn để lưu vào `last_error`.

    Chỉ CiteProofError được kèm thông điệp; thông điệp đó tuyệt đối không được chứa
    nội dung tài liệu, nguồn hay prompt. Lỗi khác chỉ ghi tên kiểu.
    """
    name = type(error).__name__
    if isinstance(error, CiteProofError):
        return f"{name}: {str(error)[:_MAX_ERROR_LENGTH]}"
    return name


def _to_claimed(row: Any) -> ClaimedJob:
    return ClaimedJob(
        id=row.id,
        run_id=row.run_id,
        stage=StageName(row.stage),
        attempts=row.attempts,
        payload=row.payload or {},
    )


_RETURNING = (Job.id, Job.run_id, Job.stage, Job.attempts, Job.payload)


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
    session: AsyncSession,
    worker_id: str,
    lock_timeout: timedelta,
    *,
    max_attempts: int,
) -> ClaimedJob | None:
    """Nhận một job sẵn sàng (hoặc job RUNNING quá hạn khóa còn lượt thử); caller commit."""
    candidate = (
        select(Job.id)
        .where(
            or_(
                and_(Job.status == JobStatus.QUEUED.value, Job.run_after <= func.now()),
                and_(
                    Job.status == JobStatus.RUNNING.value,
                    Job.locked_at < func.now() - lock_timeout,
                    Job.attempts < max_attempts,
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
        .returning(*_RETURNING)
        .execution_options(synchronize_session=False)
    )
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else _to_claimed(row)


async def expire_stale_jobs(
    session: AsyncSession, lock_timeout: timedelta, max_attempts: int
) -> list[ClaimedJob]:
    """Đánh dấu FAILED (LeaseExpired) các job RUNNING quá hạn khóa và hết lượt thử."""
    stmt = (
        update(Job)
        .where(
            Job.status == JobStatus.RUNNING.value,
            Job.locked_at < func.now() - lock_timeout,
            Job.attempts >= max_attempts,
        )
        .values(
            status=JobStatus.FAILED.value,
            last_error="LeaseExpired",
            locked_at=None,
            locked_by=None,
        )
        .returning(*_RETURNING)
        .execution_options(synchronize_session=False)
    )
    return [_to_claimed(row) for row in (await session.execute(stmt)).all()]


def _fence(job: ClaimedJob) -> tuple[Any, ...]:
    return (
        Job.id == job.id,
        Job.status == JobStatus.RUNNING.value,
        Job.attempts == job.attempts,
    )


async def mark_done(session: AsyncSession, job: ClaimedJob) -> bool:
    """Trả về False nếu lease đã mất (job được worker khác nhận lại)."""
    result = await session.execute(
        update(Job)
        .where(*_fence(job))
        .values(status=JobStatus.DONE.value, locked_at=None, locked_by=None)
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def mark_failed(
    session: AsyncSession,
    job: ClaimedJob,
    error: BaseException,
    *,
    retryable: bool,
    max_attempts: int,
    backoff_base_seconds: float,
) -> JobStatus | None:
    """Đặt lại lịch (QUEUED + backoff) nếu còn lượt thử, ngược lại FAILED.

    Trả về None nếu lease đã mất.
    """
    values: dict[str, Any] = {
        "last_error": describe_error(error),
        "locked_at": None,
        "locked_by": None,
    }
    if retryable and job.attempts < max_attempts:
        status = JobStatus.QUEUED
        values["run_after"] = func.now() + backoff_delay(job.attempts, backoff_base_seconds)
    else:
        status = JobStatus.FAILED
    values["status"] = status.value
    result = await session.execute(
        update(Job)
        .where(*_fence(job))
        .values(**values)
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    )
    return status if result.scalar_one_or_none() is not None else None
