"""Hàng đợi job trên PostgreSQL (claim bằng FOR UPDATE SKIP LOCKED).

Mọi so sánh thời gian dùng đồng hồ của database (`now()`; riêng gia hạn lease dùng
`clock_timestamp()`), để worker trên nhiều máy luôn thống nhất. Lease token của một
lần nhận job là `locked_at` (thời điểm claim); mọi ghi kết quả đều được rào bằng
token này. Không dùng `attempts` làm token vì `resume_run` đặt lại `attempts=0`.
Hạn lease nằm ở cột riêng `lease_expires_at`, được worker gia hạn (`renew_lease`)
khi stage còn chạy; job RUNNING chỉ bị coi là hết hạn khi `lease_expires_at < now()`.
Các hàm không commit (trừ `fenced_session`): caller quyết định ranh giới giao dịch.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, StageName
from citeproof.core.errors import CiteProofError, LeaseLostError
from citeproof.db.models.jobs import Job

_MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    run_id: uuid.UUID
    stage: StageName
    attempts: int
    payload: dict[str, Any]
    locked_at: datetime


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
        locked_at=row.locked_at,
    )


_RETURNING = (Job.id, Job.run_id, Job.stage, Job.attempts, Job.payload, Job.locked_at)


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
    lease: timedelta,
    *,
    max_attempts: int,
) -> ClaimedJob | None:
    """Nhận một job sẵn sàng (hoặc job RUNNING hết hạn lease còn lượt thử); caller commit."""
    candidate = (
        select(Job.id)
        .where(
            or_(
                and_(Job.status == JobStatus.QUEUED.value, Job.run_after <= func.now()),
                and_(
                    Job.status == JobStatus.RUNNING.value,
                    Job.lease_expires_at < func.now(),
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
            heartbeat_at=func.now(),
            lease_expires_at=func.now() + lease,
            started_at=func.now(),
            finished_at=None,
        )
        .returning(*_RETURNING)
        .execution_options(synchronize_session=False)
    )
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else _to_claimed(row)


def _fence(job: ClaimedJob) -> tuple[Any, ...]:
    return (
        Job.id == job.id,
        Job.status == JobStatus.RUNNING.value,
        Job.locked_at == job.locked_at,
    )


async def renew_lease(session: AsyncSession, job: ClaimedJob, lease: timedelta) -> bool:
    """Gia hạn lease thêm `lease` tính từ bây giờ; caller commit.

    Tính từ `clock_timestamp()` (giờ thật lúc cập nhật), không phải `now()` (giờ bắt
    đầu giao dịch): heartbeat phải chờ khóa lâu vẫn không ghi một hạn đã ở quá khứ.
    Trả về False nếu lease đã mất (job được nhận lại, kết thúc hoặc bị đánh hết hạn).
    """
    result = await session.execute(
        update(Job)
        .where(*_fence(job))
        .values(
            heartbeat_at=func.clock_timestamp(),
            lease_expires_at=func.clock_timestamp() + lease,
        )
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


@asynccontextmanager
async def fenced_session(
    session_factory: async_sessionmaker[AsyncSession], lease: ClaimedJob
) -> AsyncIterator[AsyncSession]:
    """Giao dịch ghi kết quả của stage, chỉ khi `lease` vẫn còn giữ job.

    Khóa `FOR KEY SHARE` dòng job theo token `locked_at` đến hết giao dịch: xung đột
    với `FOR UPDATE` của claim và của đánh hết hạn, nên không worker nào nhận lại
    được job trong lúc đang ghi, nhưng không chặn UPDATE heartbeat (không đổi khóa)
    của chính job này. Ném `LeaseLostError` nếu lease đã mất; commit khi thoát bình
    thường, rollback khi có lỗi.

    Giữ giao dịch thật ngắn: chỉ ghi DB, không bao giờ gọi mạng, GROBID hay LLM bên
    trong. Suốt giao dịch, dòng job và các dòng được khóa ngoại tham chiếu (ví dụ dòng
    run) đều bị khóa.
    """
    async with session_factory() as session, session.begin():
        held = await session.scalar(
            select(Job.id).where(*_fence(lease)).with_for_update(read=True, key_share=True)
        )
        if held is None:
            raise LeaseLostError("Lease của job đã mất")
        yield session


def stale_exhausted(max_attempts: int) -> ColumnElement[bool]:
    """Job RUNNING đã hết hạn lease và hết lượt thử (không được claim lại nữa)."""
    return and_(
        Job.status == JobStatus.RUNNING.value,
        Job.lease_expires_at < func.now(),
        Job.attempts >= max_attempts,
    )


async def expire_stale_jobs(session: AsyncSession, max_attempts: int) -> list[ClaimedJob]:
    """Đánh dấu FAILED (LeaseExpired) các job RUNNING hết hạn lease và hết lượt thử.

    Trả về các lease đã hết hạn, kèm `locked_at` trước khi bị xóa.
    """
    rows = (
        await session.execute(
            select(*_RETURNING)
            .where(stale_exhausted(max_attempts))
            .order_by(Job.id)
            .with_for_update()
        )
    ).all()
    if not rows:
        return []
    await session.execute(
        update(Job)
        .where(Job.id.in_([row.id for row in rows]))
        .values(
            status=JobStatus.FAILED.value,
            last_error="LeaseExpired",
            locked_at=None,
            locked_by=None,
            finished_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    return [_to_claimed(row) for row in rows]


async def mark_done(session: AsyncSession, job: ClaimedJob) -> bool:
    """Trả về False nếu lease đã mất (job hết hạn hoặc được nhận lại)."""
    result = await session.execute(
        update(Job)
        .where(*_fence(job))
        .values(status=JobStatus.DONE.value, locked_at=None, locked_by=None, finished_at=func.now())
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
        "finished_at": func.now(),
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
