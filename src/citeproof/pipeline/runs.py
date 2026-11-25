"""Vòng đời lần chạy: tạo, hoàn tất/thất bại từng stage, tiếp tục sau lỗi.

Mọi thay đổi khóa dòng `verification_runs` (`SELECT … FOR UPDATE`) trước khi cập
nhật job. Các worker song song vì thế luôn khóa theo cùng thứ tự run → job (không
deadlock) và việc kiểm tra phụ thuộc của một run được tuần tự hóa (S5 chỉ được
thêm đúng một lần). Run đã FAILED hoặc CANCELLED không bao giờ bị ghi đè trạng
thái. Các hàm không commit: caller quyết định ranh giới giao dịch.
"""

from collections.abc import Collection, Mapping, Sequence
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, insert, null, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import JobStatus, RunStatus, StageName
from citeproof.core.errors import InputError, RetryableError
from citeproof.core.settings import Settings
from citeproof.db.models.jobs import Job
from citeproof.db.models.runs import VerificationRun
from citeproof.pipeline.jobs import (
    ClaimedJob,
    describe_error,
    enqueue,
    expire_stale_jobs,
    mark_done,
    mark_failed,
    stale_exhausted,
)
from citeproof.pipeline.stages import STAGE_DEPENDENCIES, StageResult

_ACTIVE = frozenset({RunStatus.QUEUED.value, RunStatus.RUNNING.value})
_FINAL_STAGE = StageName.S9
_LEASE_EXPIRED = "LeaseExpired"
_SKIPPED_PAYLOAD: Mapping[str, Any] = MappingProxyType({"skipped": True})

# Lần chạy con (QĐ-30): stage bắt đầu → các stage bỏ qua (ghi DONE với `{"skipped": true}`).
# Bỏ qua đúng các phụ thuộc bắc cầu của stage bắt đầu, cùng S4 khi bắt đầu từ S3 (claim là
# kết quả cấp tài liệu, dùng lại theo QĐ-13), để S5 được thêm khi S3 xong.
PARTIAL_SKIPS: Mapping[StageName, frozenset[StageName]] = MappingProxyType(
    {
        StageName.S3: frozenset({StageName.S1, StageName.S2, StageName.S4}),
        StageName.S6: frozenset(
            {StageName.S1, StageName.S2, StageName.S3, StageName.S4, StageName.S5}
        ),
    }
)


async def create_run(
    session: AsyncSession, *, document_id: UUID, user_id: UUID, config: dict[str, Any]
) -> UUID:
    """Tạo run ở trạng thái QUEUED và đưa S1 vào hàng đợi."""
    run_id: UUID = (
        await session.execute(
            insert(VerificationRun)
            .values(
                document_id=document_id,
                user_id=user_id,
                status=RunStatus.QUEUED.value,
                config=config,
            )
            .returning(VerificationRun.id)
        )
    ).scalar_one()
    await enqueue(session, run_id, StageName.S1)
    return run_id


async def create_seeded_run(
    session: AsyncSession,
    *,
    document_id: UUID,
    user_id: UUID,
    config: dict[str, Any],
    start: StageName,
    skipped: Collection[StageName],
    parent_run_id: UUID | None = None,
) -> UUID:
    """Tạo run QUEUED: job của các stage `skipped` ghi DONE (`{"skipped": true}`), rồi đưa `start`
    vào hàng đợi. Dùng cho lần chạy con và cho bộ đánh giá (dữ liệu nhập sẵn tới S5, spec §11).
    """
    if start in skipped:
        raise ValueError(f"stage bắt đầu {start.value} không được nằm trong tập bỏ qua")
    run_id: UUID = (
        await session.execute(
            insert(VerificationRun)
            .values(
                document_id=document_id,
                user_id=user_id,
                parent_run_id=parent_run_id,
                status=RunStatus.QUEUED.value,
                config=config,
            )
            .returning(VerificationRun.id)
        )
    ).scalar_one()
    if skipped:
        await session.execute(
            insert(Job).values(
                [
                    {
                        "run_id": run_id,
                        "stage": stage.value,
                        "status": JobStatus.DONE.value,
                        "payload": dict(_SKIPPED_PAYLOAD),
                        "finished_at": func.now(),
                    }
                    for stage in sorted(skipped)
                ]
            )
        )
    await enqueue(session, run_id, start)
    return run_id


async def create_partial_run(
    session: AsyncSession,
    *,
    parent_run_id: UUID,
    user_id: UUID,
    document_id: UUID,
    config: dict[str, Any],
    start: StageName,
) -> UUID:
    """Tạo lần chạy con QUEUED: job của stage bỏ qua ghi DONE, rồi đưa `start` vào hàng đợi."""
    skipped = PARTIAL_SKIPS.get(start)
    if skipped is None:
        raise ValueError(f"lần chạy con không bắt đầu được từ {start.value}")
    return await create_seeded_run(
        session,
        document_id=document_id,
        user_id=user_id,
        config=config,
        start=start,
        skipped=skipped,
        parent_run_id=parent_run_id,
    )


async def _lock_run(session: AsyncSession, run_id: UUID) -> Row[tuple[str, list[Any]]] | None:
    return (
        await session.execute(
            select(VerificationRun.status, VerificationRun.warnings)
            .where(VerificationRun.id == run_id)
            .with_for_update()
        )
    ).one_or_none()


async def _update_run(session: AsyncSession, run_id: UUID, **values: Any) -> None:
    await session.execute(
        update(VerificationRun)
        .where(VerificationRun.id == run_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def _mark_run_failed(session: AsyncSession, run_id: UUID, error: dict[str, str]) -> None:
    await _update_run(
        session, run_id, status=RunStatus.FAILED.value, error=error, finished_at=func.now()
    )


async def _enqueue_ready_successors(session: AsyncSession, run_id: UUID, stage: StageName) -> None:
    successors = [name for name, deps in STAGE_DEPENDENCIES.items() if stage in deps]
    if not successors:
        return
    done = {
        StageName(value)
        for value in (
            await session.execute(
                select(Job.stage).where(Job.run_id == run_id, Job.status == JobStatus.DONE.value)
            )
        ).scalars()
    }
    for successor in successors:
        if STAGE_DEPENDENCIES[successor] <= done:
            await enqueue(session, run_id, successor)


def _merge_warnings(existing: list[Any], new: Sequence[str]) -> list[Any]:
    """Nối cảnh báo mới chưa có trong run (giữ thứ tự xuất hiện đầu tiên)."""
    merged = list(existing)
    for warning in new:
        if warning not in merged:
            merged.append(warning)
    return merged


async def complete_stage(session: AsyncSession, job: ClaimedJob, result: StageResult) -> bool:
    """Đánh dấu job DONE, ghi cảnh báo, chuyển run sang RUNNING và thêm stage kế tiếp.

    Trả về False nếu lease đã mất; khi đó không ghi gì. Cảnh báo đã có trong run không
    được ghi lại (nhiều stage cùng báo `LLM_NOT_CONFIGURED`). Run đã kết thúc
    (FAILED/CANCELLED) chỉ nhận thêm cảnh báo, giữ nguyên trạng thái và không có
    stage kế tiếp.
    """
    run = await _lock_run(session, job.run_id)
    if run is None or not await mark_done(session, job):
        return False
    warnings = _merge_warnings(run.warnings, result.warnings)
    if run.status not in _ACTIVE:
        if warnings != run.warnings:
            await _update_run(session, job.run_id, warnings=warnings)
        return True
    if job.stage is _FINAL_STAGE:
        status = RunStatus.COMPLETED_WITH_WARNINGS if warnings else RunStatus.COMPLETED
        await _update_run(
            session, job.run_id, status=status.value, warnings=warnings, finished_at=func.now()
        )
        return True
    await _update_run(session, job.run_id, status=RunStatus.RUNNING.value, warnings=warnings)
    await _enqueue_ready_successors(session, job.run_id, job.stage)
    return True


async def fail_stage(
    session: AsyncSession, job: ClaimedJob, error: BaseException, settings: Settings
) -> bool:
    """Đặt lại lịch job (lỗi tạm thời còn lượt thử) hoặc đánh FAILED cả job lẫn run.

    Trả về False nếu lease đã mất; khi đó không ghi gì.
    """
    run = await _lock_run(session, job.run_id)
    if run is None:
        return False
    status = await mark_failed(
        session,
        job,
        error,
        retryable=isinstance(error, RetryableError),
        max_attempts=settings.job_max_attempts,
        backoff_base_seconds=settings.job_backoff_base_seconds,
    )
    if status == JobStatus.FAILED and run.status in _ACTIVE:
        await _mark_run_failed(
            session,
            job.run_id,
            {
                "stage": job.stage.value,
                "type": type(error).__name__,
                "message": describe_error(error),
            },
        )
    return status is not None


async def fail_expired_jobs(session: AsyncSession, *, max_attempts: int) -> list[ClaimedJob]:
    """Đánh FAILED các job RUNNING hết hạn lease đã hết lượt thử, cùng run của chúng.

    Khóa trước các run liên quan (theo thứ tự id) rồi mới cập nhật job, giữ đúng thứ
    tự khóa run → job như `complete_stage`/`fail_stage`. Dùng chung điều kiện
    `stale_exhausted` với `expire_stale_jobs` nên tập job hết hạn luôn nằm trong tập
    run đã khóa (`now()` cố định trong một giao dịch).
    """
    stale_run_ids = select(Job.run_id).where(stale_exhausted(max_attempts))
    await session.execute(
        select(VerificationRun.id)
        .where(VerificationRun.id.in_(stale_run_ids))
        .order_by(VerificationRun.id)
        .with_for_update()
    )
    expired = await expire_stale_jobs(session, max_attempts)
    for job in expired:
        run = await _lock_run(session, job.run_id)
        if run is not None and run.status in _ACTIVE:
            await _mark_run_failed(
                session,
                job.run_id,
                {"stage": job.stage.value, "type": _LEASE_EXPIRED, "message": _LEASE_EXPIRED},
            )
    return expired


async def resume_run(session: AsyncSession, run_id: UUID) -> int:
    """Đưa các job FAILED của run về QUEUED và chạy tiếp; trả về số job được đưa lại."""
    run = await _lock_run(session, run_id)
    if run is None or run.status != RunStatus.FAILED.value:
        raise InputError("Chỉ tiếp tục được lần chạy đang ở trạng thái FAILED")
    failed_jobs = (Job.run_id == run_id, Job.status == JobStatus.FAILED.value)
    if await session.scalar(select(Job.id).where(*failed_jobs).limit(1)) is None:
        raise InputError("Lần chạy không có giai đoạn FAILED nào để chạy lại")
    requeued = (
        await session.execute(
            update(Job)
            .where(*failed_jobs)
            .values(
                status=JobStatus.QUEUED.value,
                attempts=0,
                run_after=func.now(),
                locked_at=None,
                locked_by=None,
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    await _update_run(
        session, run_id, status=RunStatus.RUNNING.value, error=null(), finished_at=None
    )
    return len(requeued)
