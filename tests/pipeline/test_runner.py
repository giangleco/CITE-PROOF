import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, RunStatus, StageName
from citeproof.core.errors import InputError, RetryableError
from citeproof.core.logging import ALLOWED_LOG_FIELDS, configure_logging
from citeproof.core.settings import Settings
from citeproof.db.models import Job, VerificationRun
from citeproof.pipeline import worker as worker_module
from citeproof.pipeline.jobs import ClaimedJob, claim_next, enqueue
from citeproof.pipeline.runs import complete_stage, create_run, fail_stage, resume_run
from citeproof.pipeline.stages import (
    STAGE_DEPENDENCIES,
    NoopStage,
    StageContext,
    StageRegistry,
    StageResult,
)
from citeproof.pipeline.worker import run_worker, work_once
from tests.db.factories import make_document
from tests.pipeline.helpers import (
    CountingStage,
    FailingStage,
    counting_registry,
    drain,
    pipeline_settings,
    stage_context,
)

S = StageName
SessionFactory = async_sessionmaker[AsyncSession]
TERMINAL = {
    RunStatus.COMPLETED,
    RunStatus.COMPLETED_WITH_WARNINGS,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


async def _new_run(session_factory: SessionFactory) -> uuid.UUID:
    async with session_factory() as session:
        doc = await make_document(session)
        run_id = await create_run(
            session, document_id=doc.id, user_id=doc.user_id, config={"variant": "V2"}
        )
        await session.commit()
    return run_id


async def _run(session_factory: SessionFactory, run_id: uuid.UUID) -> VerificationRun:
    async with session_factory() as session:
        return (
            await session.execute(select(VerificationRun).where(VerificationRun.id == run_id))
        ).scalar_one()


async def _jobs(session_factory: SessionFactory, run_id: uuid.UUID) -> dict[StageName, Job]:
    async with session_factory() as session:
        rows = (await session.execute(select(Job).where(Job.run_id == run_id))).scalars()
        return {StageName(job.stage): job for job in rows}


async def _s5_rows(session_factory: SessionFactory, run_id: uuid.UUID) -> int | None:
    async with session_factory() as session:
        return await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.run_id == run_id, Job.stage == S.S5.value)
        )


async def _until_status(
    session_factory: SessionFactory, run_id: uuid.UUID, status: RunStatus
) -> None:
    while True:
        if (await _run(session_factory, run_id)).status == status.value:
            return
        await asyncio.sleep(0.01)


async def _work_until_terminal(
    session_factory: SessionFactory,
    registry: StageRegistry,
    run_id: uuid.UUID,
    worker_ids: tuple[str, ...] = ("w1", "w2"),
) -> None:
    """Chạy song song một vòng `work_once` cho mỗi worker đến khi run kết thúc."""
    settings = pipeline_settings()

    async def loop(worker_id: str) -> None:
        while True:
            if await work_once(session_factory, registry, worker_id, settings):
                continue
            if RunStatus((await _run(session_factory, run_id)).status) in TERMINAL:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(asyncio.gather(*(loop(w) for w in worker_ids)), timeout=30)


def _log_records(err: str) -> list[dict[str, object]]:
    records = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    base_fields = {"timestamp", "level", "module", "event"}
    assert all(set(record) <= ALLOWED_LOG_FIELDS | base_fields for record in records)
    return records


# --- DAG và registry -------------------------------------------------------------


def test_stage_dependencies_match_dag() -> None:
    assert dict(STAGE_DEPENDENCIES) == {
        S.S1: frozenset(),
        S.S2: frozenset({S.S1}),
        S.S3: frozenset({S.S2}),
        S.S4: frozenset({S.S2}),
        S.S5: frozenset({S.S3, S.S4}),
        S.S6: frozenset({S.S5}),
        S.S7: frozenset({S.S6}),
        S.S8: frozenset({S.S7}),
        S.S9: frozenset({S.S8}),
    }


def test_registry_requires_every_stage_exactly_once() -> None:
    stages = [NoopStage(name) for name in StageName]
    with pytest.raises(ValueError):
        StageRegistry(stages[:-1])
    with pytest.raises(ValueError):
        StageRegistry([*stages, NoopStage(S.S1)])
    registry = StageRegistry.noop()
    for name in StageName:
        assert registry.get(name).name == name


async def test_noop_stage_returns_empty_result() -> None:
    assert await NoopStage(S.S3).run(stage_context(uuid.uuid4(), stage=S.S3)) == StageResult()


def test_stage_context_attempts() -> None:
    first = stage_context(uuid.uuid4(), attempts=1, max_attempts=3)
    assert (first.attempt, first.is_last_attempt) == (1, False)
    last = stage_context(uuid.uuid4(), attempts=3, max_attempts=3)
    assert (last.attempt, last.is_last_attempt) == (3, True)
    assert last.run_id == last.lease.run_id


def test_settings_reject_heartbeat_too_slow() -> None:
    with pytest.raises(ValidationError):
        Settings(job_lock_timeout_seconds=60, job_heartbeat_seconds=30)
    assert Settings(job_lock_timeout_seconds=61, job_heartbeat_seconds=30).job_heartbeat_seconds


# --- Runner end-to-end -----------------------------------------------------------


@pytest.mark.db
async def test_create_run_queues_s1(session_factory: SessionFactory, clean_db: None) -> None:
    run_id = await _new_run(session_factory)
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.QUEUED.value
    assert run.config == {"variant": "V2"}
    assert (run.warnings, run.error, run.current_stage) == ([], None, None)
    jobs = await _jobs(session_factory, run_id)
    assert list(jobs) == [S.S1]
    assert jobs[S.S1].status == JobStatus.QUEUED.value


@pytest.mark.db
async def test_empty_run_completes_end_to_end(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    registry = StageRegistry.noop()
    run_id = await _new_run(session_factory)

    snapshots: list[dict[StageName, str]] = []
    run_statuses: list[str] = []
    while await work_once(session_factory, registry, "w1", settings):
        jobs = await _jobs(session_factory, run_id)
        snapshots.append({stage: job.status for stage, job in jobs.items()})
        run_statuses.append((await _run(session_factory, run_id)).status)

    assert len(snapshots) == 9
    assert run_statuses[:-1] == [RunStatus.RUNNING.value] * 8
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.COMPLETED.value
    assert run.finished_at is not None
    assert (run.warnings, run.error, run.current_stage) == ([], None, None)
    jobs = await _jobs(session_factory, run_id)
    assert set(jobs) == set(StageName)
    assert {job.status for job in jobs.values()} == {JobStatus.DONE.value}
    # S5 chỉ xuất hiện khi cả S3 lẫn S4 đã DONE.
    done = JobStatus.DONE.value
    for snapshot in snapshots:
        if S.S5 in snapshot:
            assert snapshot[S.S3] == snapshot[S.S4] == done
    assert any(
        S.S5 not in snapshot and done in (snapshot.get(S.S3), snapshot.get(S.S4))
        for snapshot in snapshots
    )


@pytest.mark.db
async def test_run_fails_at_s5_and_resumes_from_s5(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    registry, stages = counting_registry(FailingStage(S.S5, 1, InputError("boom")))
    run_id = await _new_run(session_factory)

    assert await drain(session_factory, registry, settings) == 5
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error == {"stage": "S5", "type": "InputError", "message": "InputError: boom"}
    assert run.finished_at is not None
    jobs = await _jobs(session_factory, run_id)
    assert set(jobs) == {S.S1, S.S2, S.S3, S.S4, S.S5}
    assert jobs[S.S5].status == JobStatus.FAILED.value

    async with session_factory() as session:
        assert await resume_run(session, run_id) == 1
        await session.commit()
    run = await _run(session_factory, run_id)
    assert (run.status, run.error, run.finished_at) == (RunStatus.RUNNING.value, None, None)
    s5 = (await _jobs(session_factory, run_id))[S.S5]
    assert (s5.status, s5.attempts) == (JobStatus.QUEUED.value, 0)

    assert await drain(session_factory, registry, settings) == 5
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.COMPLETED.value
    assert run.error is None
    calls = {name: len(stage.calls) for name, stage in stages.items()}
    assert calls == {name: 2 if name is S.S5 else 1 for name in StageName}


@pytest.mark.db
async def test_resume_rejects_run_that_is_not_failed(
    session_factory: SessionFactory, clean_db: None
) -> None:
    run_id = await _new_run(session_factory)
    async with session_factory() as session:
        with pytest.raises(InputError):
            await resume_run(session, run_id)
        with pytest.raises(InputError):
            await resume_run(session, uuid.uuid4())
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.QUEUED.value


@pytest.mark.db
async def test_retryable_error_is_rescheduled_not_failed(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    registry, stages = counting_registry(FailingStage(S.S2, 1, RetryableError("429")))
    run_id = await _new_run(session_factory)

    assert await drain(session_factory, registry, settings) == 2
    async with session_factory() as session:
        job = (
            await session.execute(select(Job).where(Job.run_id == run_id, Job.stage == "S2"))
        ).scalar_one()
        in_future = await session.scalar(select(Job.run_after > func.now()).where(Job.id == job.id))
    assert (job.status, job.attempts) == (JobStatus.QUEUED.value, 1)
    assert job.last_error == "RetryableError: 429"
    assert in_future is True
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (RunStatus.RUNNING.value, None)

    async with session_factory() as session:
        await session.execute(update(Job).where(Job.id == job.id).values(run_after=func.now()))
        await session.commit()
    assert await drain(session_factory, registry, settings) == 8
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.COMPLETED.value
    contexts = stages[S.S2].calls
    assert [(c.run_id, c.lease.stage, c.attempt) for c in contexts] == [
        (run_id, S.S2, 1),
        (run_id, S.S2, 2),
    ]
    assert [c.is_last_attempt for c in contexts] == [False, False]


@pytest.mark.db
async def test_warnings_make_completed_with_warnings(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    registry, _ = counting_registry(
        CountingStage(S.S3, warnings=["ref 3 lỗi tra cứu"]),
        CountingStage(S.S8, warnings=["vượt hạn mức claim"]),
    )
    run_id = await _new_run(session_factory)

    assert await drain(session_factory, registry, settings) == 9
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.COMPLETED_WITH_WARNINGS.value
    assert run.warnings == ["ref 3 lỗi tra cứu", "vượt hạn mức claim"]
    assert run.finished_at is not None


class BarrierStage(CountingStage):
    """Lần gọi đầu chờ đủ bên ở barrier (buộc chạy song song) rồi ném `error` nếu có."""

    def __init__(
        self, name: StageName, barrier: asyncio.Barrier, error: Exception | None = None
    ) -> None:
        super().__init__(name)
        self.barrier = barrier
        self.error = error

    async def run(self, ctx: StageContext) -> StageResult:
        result = await super().run(ctx)
        if len(self.calls) == 1:
            await self.barrier.wait()
            if self.error is not None:
                raise self.error
        return result


@pytest.mark.db
async def test_concurrent_workers_enqueue_s5_once(
    session_factory: SessionFactory, clean_db: None
) -> None:
    barrier = asyncio.Barrier(2)
    registry, stages = counting_registry(BarrierStage(S.S3, barrier), BarrierStage(S.S4, barrier))
    run_id = await _new_run(session_factory)

    await _work_until_terminal(session_factory, registry, run_id)

    assert await _s5_rows(session_factory, run_id) == 1
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.COMPLETED.value
    jobs = await _jobs(session_factory, run_id)
    assert {job.status for job in jobs.values()} == {JobStatus.DONE.value}
    assert all(len(stage.calls) == 1 for stage in stages.values())


@pytest.mark.db
async def test_s3_fails_while_s4_completes_then_resume_completes(
    session_factory: SessionFactory, clean_db: None
) -> None:
    barrier = asyncio.Barrier(2)
    registry, stages = counting_registry(
        BarrierStage(S.S3, barrier, error=InputError("ref hỏng")),
        BarrierStage(S.S4, barrier),
    )
    run_id = await _new_run(session_factory)

    await _work_until_terminal(session_factory, registry, run_id)
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None and run.error["stage"] == "S3"
    jobs = await _jobs(session_factory, run_id)
    assert (jobs[S.S3].status, jobs[S.S4].status) == (JobStatus.FAILED.value, JobStatus.DONE.value)
    assert S.S5 not in jobs

    async with session_factory() as session:
        assert await resume_run(session, run_id) == 1
        await session.commit()
    assert await drain(session_factory, registry, pipeline_settings()) == 6
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (RunStatus.COMPLETED.value, None)
    assert await _s5_rows(session_factory, run_id) == 1
    assert (len(stages[S.S3].calls), len(stages[S.S4].calls)) == (2, 1)


@pytest.mark.db
@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.CANCELLED])
async def test_completing_stage_of_terminal_run_does_not_advance_it(
    session_factory: SessionFactory, clean_db: None, status: RunStatus
) -> None:
    settings = pipeline_settings()
    registry, _ = counting_registry(CountingStage(S.S4, warnings=["cảnh báo muộn"]))
    run_id = await _new_run(session_factory)
    error = {"stage": "S3", "type": "InputError", "message": "InputError: x"}
    run_error = error if status is RunStatus.FAILED else None
    async with session_factory() as session:
        # S1..S3 đã xong, S4 đang chờ; run bị đặt trạng thái kết thúc trực tiếp.
        for stage in (S.S2, S.S3, S.S4):
            await enqueue(session, run_id, stage)
        await session.execute(
            update(Job)
            .where(Job.run_id == run_id, Job.stage.in_(["S1", "S2", "S3"]))
            .values(status=JobStatus.DONE.value)
        )
        await session.execute(
            update(VerificationRun)
            .where(VerificationRun.id == run_id)
            .values(status=status.value, error=run_error)
        )
        await session.commit()

    assert await drain(session_factory, registry, settings) == 1
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (status.value, run_error)
    # Kết quả của stage đã xong vẫn được ghi lại (giữ cho lần resume).
    assert run.warnings == ["cảnh báo muộn"]
    jobs = await _jobs(session_factory, run_id)
    assert jobs[S.S4].status == JobStatus.DONE.value
    assert S.S5 not in jobs


async def _claim_then_lose_lease(
    session_factory: SessionFactory,
) -> tuple[ClaimedJob, ClaimedJob]:
    """Worker cũ nhận S1, quá hạn khóa, worker mới nhận lại; trả về (lease cũ, lease mới)."""
    async with session_factory() as session:
        old = await claim_next(session, "w-old", timedelta(minutes=15), max_attempts=3)
        assert old is not None
        await session.execute(
            update(Job)
            .where(Job.id == old.id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        await session.commit()
        new = await claim_next(session, "w-new", timedelta(minutes=15), max_attempts=3)
        await session.commit()
    assert new is not None and (new.id, new.attempts) == (old.id, 2)
    return old, new


@pytest.mark.db
async def test_completion_after_lost_lease_is_ignored(
    session_factory: SessionFactory, clean_db: None
) -> None:
    run_id = await _new_run(session_factory)
    old, new = await _claim_then_lose_lease(session_factory)
    async with session_factory() as session:
        assert await complete_stage(session, old, StageResult(warnings=("muộn",))) is False
        await session.commit()
    run = await _run(session_factory, run_id)
    assert (run.status, run.warnings) == (RunStatus.QUEUED.value, [])
    jobs = await _jobs(session_factory, run_id)
    assert list(jobs) == [S.S1]
    assert (jobs[S.S1].status, jobs[S.S1].locked_by) == (JobStatus.RUNNING.value, "w-new")

    async with session_factory() as session:
        assert await complete_stage(session, new, StageResult()) is True
        await session.commit()
    jobs = await _jobs(session_factory, run_id)
    assert (jobs[S.S1].status, jobs[S.S2].status) == (JobStatus.DONE.value, JobStatus.QUEUED.value)
    assert (await _run(session_factory, run_id)).status == RunStatus.RUNNING.value


@pytest.mark.db
async def test_failure_after_lost_lease_is_ignored(
    session_factory: SessionFactory, clean_db: None
) -> None:
    run_id = await _new_run(session_factory)
    old, _ = await _claim_then_lose_lease(session_factory)
    async with session_factory() as session:
        assert await fail_stage(session, old, InputError("muộn"), pipeline_settings()) is False
        await session.commit()
    run = await _run(session_factory, run_id)
    assert (run.status, run.error, run.finished_at) == (RunStatus.QUEUED.value, None, None)
    job = (await _jobs(session_factory, run_id))[S.S1]
    assert (job.status, job.locked_by, job.last_error) == (JobStatus.RUNNING.value, "w-new", None)


@pytest.mark.db
async def test_stale_lease_from_before_resume_is_fenced(
    session_factory: SessionFactory, clean_db: None
) -> None:
    # Hồi quy ABA: resume đặt attempts=0 nên attempts không đủ làm lease token.
    run_id = await _new_run(session_factory)
    stale, current = await _claim_then_lose_lease(session_factory)
    async with session_factory() as session:
        assert await fail_stage(session, current, InputError("x"), pipeline_settings()) is True
        await session.commit()
    async with session_factory() as session:
        assert await resume_run(session, run_id) == 1
        await session.commit()
    async with session_factory() as session:
        resumed = await claim_next(session, "w-C", timedelta(minutes=15), max_attempts=3)
        await session.commit()
    assert resumed is not None and resumed.attempts == stale.attempts == 1

    # Worker A (treo từ trước resume) xong với lease cũ: phải bị chặn.
    async with session_factory() as session:
        assert await complete_stage(session, stale, StageResult()) is False
        assert await fail_stage(session, stale, InputError("muộn"), pipeline_settings()) is False
        await session.commit()
    jobs = await _jobs(session_factory, run_id)
    assert list(jobs) == [S.S1]
    assert (jobs[S.S1].status, jobs[S.S1].locked_by) == (JobStatus.RUNNING.value, "w-C")
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (RunStatus.RUNNING.value, None)


@pytest.mark.db
async def test_resume_rejects_failed_run_without_failed_jobs(
    session_factory: SessionFactory, clean_db: None
) -> None:
    run_id = await _new_run(session_factory)
    error = {"stage": "S1", "type": "InputError", "message": "InputError: x"}
    async with session_factory() as session:
        await session.execute(
            update(VerificationRun)
            .where(VerificationRun.id == run_id)
            .values(status=RunStatus.FAILED.value, error=error)
        )
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(InputError):
            await resume_run(session, run_id)
        await session.commit()
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (RunStatus.FAILED.value, error)
    assert (await _jobs(session_factory, run_id))[S.S1].status == JobStatus.QUEUED.value


class LeaseLosingStage(CountingStage):
    """Trong lúc stage chạy, worker khác nhận lại job (lease token đổi)."""

    def __init__(
        self, name: StageName, session_factory: SessionFactory, error: Exception | None
    ) -> None:
        super().__init__(name)
        self.session_factory = session_factory
        self.error = error

    async def run(self, ctx: StageContext) -> StageResult:
        result = await super().run(ctx)
        async with self.session_factory() as session:
            await session.execute(
                update(Job)
                .where(Job.run_id == ctx.run_id, Job.stage == self.name.value)
                .values(locked_at=func.now(), locked_by="w2")
            )
            await session.commit()
        if self.error is not None:
            raise self.error
        return result


@pytest.mark.db
@pytest.mark.parametrize("error", [None, InputError("x")], ids=["completed", "failed"])
async def test_result_of_lost_lease_is_discarded_and_logged(
    session_factory: SessionFactory,
    clean_db: None,
    capsys: pytest.CaptureFixture[str],
    error: Exception | None,
) -> None:
    configure_logging("INFO")
    registry, _ = counting_registry(LeaseLosingStage(S.S1, session_factory, error))
    run_id = await _new_run(session_factory)

    assert await work_once(session_factory, registry, "w1", pipeline_settings()) is True
    jobs = await _jobs(session_factory, run_id)
    assert list(jobs) == [S.S1]
    assert (jobs[S.S1].status, jobs[S.S1].locked_by) == (JobStatus.RUNNING.value, "w2")
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (RunStatus.QUEUED.value, None)
    events = [record["event"] for record in _log_records(capsys.readouterr().err)]
    assert "stage_result_discarded" in events
    assert "stage_completed" not in events


@pytest.mark.db
async def test_unexpected_stage_error_fails_run_without_leaking_message(
    session_factory: SessionFactory, clean_db: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    settings = pipeline_settings()
    registry, _ = counting_registry(FailingStage(S.S1, 1, ValueError("nội dung bí mật")))
    run_id = await _new_run(session_factory)

    assert await drain(session_factory, registry, settings) == 1
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error == {"stage": "S1", "type": "ValueError", "message": "ValueError"}
    job = (await _jobs(session_factory, run_id))[S.S1]
    assert (job.status, job.attempts) == (JobStatus.FAILED.value, 1)

    err = capsys.readouterr().err
    assert "bí mật" not in err
    records = _log_records(err)
    failed = [record for record in records if record["event"] == "stage_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == "ValueError"
    assert (failed[0]["run_id"], failed[0]["stage"]) == (str(run_id), "S1")
    assert failed[0]["worker_id"] == "w1"


@pytest.mark.db
@pytest.mark.parametrize(
    ("initial", "expected"),
    [(RunStatus.RUNNING, RunStatus.FAILED), (RunStatus.CANCELLED, RunStatus.CANCELLED)],
)
async def test_expired_lease_fails_job_and_run(
    session_factory: SessionFactory, clean_db: None, initial: RunStatus, expected: RunStatus
) -> None:
    settings = pipeline_settings(job_max_attempts=1)
    run_id = await _new_run(session_factory)
    async with session_factory() as session:
        job = await claim_next(session, "w-dead", timedelta(minutes=15), max_attempts=1)
        assert job is not None
        await session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        await session.execute(
            update(VerificationRun).where(VerificationRun.id == run_id).values(status=initial.value)
        )
        await session.commit()
    registry, stages = counting_registry()

    assert await work_once(session_factory, registry, "w1", settings) is False
    run = await _run(session_factory, run_id)
    assert run.status == expected.value
    if expected is RunStatus.FAILED:
        assert run.error == {"stage": "S1", "type": "LeaseExpired", "message": "LeaseExpired"}
        assert run.finished_at is not None
    else:
        assert (run.error, run.finished_at) == (None, None)
    row = (await _jobs(session_factory, run_id))[S.S1]
    assert (row.status, row.last_error) == (JobStatus.FAILED.value, "LeaseExpired")
    assert row.finished_at is not None
    assert stages[S.S1].calls == []


class SleepingStage(CountingStage):
    """Chạy lâu hơn độ dài lease; báo `started` khi bắt đầu."""

    def __init__(self, name: StageName, seconds: float) -> None:
        super().__init__(name)
        self.seconds = seconds
        self.started = asyncio.Event()

    async def run(self, ctx: StageContext) -> StageResult:
        result = await super().run(ctx)
        self.started.set()
        await asyncio.sleep(self.seconds)
        return result


@pytest.mark.db
async def test_heartbeat_keeps_long_stage_alive(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings(job_lock_timeout_seconds=1, job_heartbeat_seconds=0.2)
    stage = SleepingStage(S.S1, 1.5)
    registry, _ = counting_registry(stage)
    run_id = await _new_run(session_factory)

    work = asyncio.create_task(work_once(session_factory, registry, "w1", settings))
    await asyncio.wait_for(stage.started.wait(), timeout=5)
    await asyncio.sleep(1.2)  # quá độ dài lease ban đầu (1 s)
    async with session_factory() as session:
        stolen = await claim_next(session, "w2", timedelta(seconds=1), max_attempts=3)
        await session.commit()
    assert stolen is None
    assert await asyncio.wait_for(work, timeout=10) is True

    job = (await _jobs(session_factory, run_id))[S.S1]
    assert (job.status, job.attempts) == (JobStatus.DONE.value, 1)
    assert job.started_at is not None and job.finished_at is not None
    assert job.finished_at - job.started_at >= timedelta(seconds=1.5)
    assert job.heartbeat_at is not None and job.heartbeat_at > job.started_at


class BlockedStage(CountingStage):
    """Chờ một Event không bao giờ được đặt; ghi nhận khi bị hủy."""

    def __init__(self, name: StageName) -> None:
        super().__init__(name)
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self, ctx: StageContext) -> StageResult:
        await super().run(ctx)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return StageResult(warnings=("không được ghi",))


@pytest.mark.db
async def test_lost_lease_cancels_stage_and_discards_result(
    session_factory: SessionFactory, clean_db: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    settings = pipeline_settings(job_heartbeat_seconds=0.05)
    stage = BlockedStage(S.S1)
    registry, _ = counting_registry(stage)
    run_id = await _new_run(session_factory)

    work = asyncio.create_task(work_once(session_factory, registry, "w1", settings))
    await asyncio.wait_for(stage.started.wait(), timeout=5)
    # Lease hết hạn và worker khác nhận lại trong cùng một giao dịch (heartbeat chen
    # vào sẽ phải chờ khóa dòng, rồi thấy token đã đổi).
    async with session_factory() as session:
        job_id = (await _jobs(session_factory, run_id))[S.S1].id
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        stolen = await claim_next(session, "w2", timedelta(minutes=15), max_attempts=3)
        await session.commit()
    assert stolen is not None and (stolen.id, stolen.attempts) == (job_id, 2)

    assert await asyncio.wait_for(work, timeout=10) is True
    assert stage.cancelled
    jobs = await _jobs(session_factory, run_id)
    assert list(jobs) == [S.S1]
    assert (jobs[S.S1].status, jobs[S.S1].locked_by) == (JobStatus.RUNNING.value, "w2")
    run = await _run(session_factory, run_id)
    assert (run.status, run.warnings, run.error) == (RunStatus.QUEUED.value, [], None)
    records = _log_records(capsys.readouterr().err)
    events = [record["event"] for record in records]
    assert "stage_lease_lost" in events
    assert not {"stage_completed", "stage_failed", "stage_result_discarded"} & set(events)
    lost = next(record for record in records if record["event"] == "stage_lease_lost")
    assert (lost["run_id"], lost["stage"], lost["worker_id"]) == (str(run_id), "S1", "w1")


@pytest.mark.db
async def test_heartbeat_error_is_logged_and_retried(
    session_factory: SessionFactory,
    clean_db: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_logging("INFO")
    real_renew = worker_module.renew_lease
    calls = 0

    async def flaky_renew(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("UPDATE bí mật", {}, Exception("mất kết nối"))
        return await real_renew(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_module, "renew_lease", flaky_renew)
    settings = pipeline_settings(job_lock_timeout_seconds=1, job_heartbeat_seconds=0.1)
    registry, _ = counting_registry(SleepingStage(S.S1, 0.5))
    run_id = await _new_run(session_factory)

    assert await work_once(session_factory, registry, "w1", settings) is True
    assert calls >= 2
    assert (await _jobs(session_factory, run_id))[S.S1].status == JobStatus.DONE.value
    err = capsys.readouterr().err
    assert "bí mật" not in err and "mất kết nối" not in err
    failures = [r for r in _log_records(err) if r["event"] == "lease_renewal_failed"]
    assert len(failures) == 1
    assert (failures[0]["error_type"], failures[0]["run_id"]) == ("OperationalError", str(run_id))


@pytest.mark.db
async def test_cancelled_error_propagates_and_keeps_lease(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    registry, _ = counting_registry(FailingStage(S.S1, 1, asyncio.CancelledError()))
    run_id = await _new_run(session_factory)

    with pytest.raises(asyncio.CancelledError):
        await work_once(session_factory, registry, "w1", settings)
    job = (await _jobs(session_factory, run_id))[S.S1]
    assert (job.status, job.locked_by) == (JobStatus.RUNNING.value, "w1")
    run = await _run(session_factory, run_id)
    assert (run.status, run.error) == (RunStatus.QUEUED.value, None)


@pytest.mark.db
async def test_run_worker_completes_run_and_stops(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    run_id = await _new_run(session_factory)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(session_factory, StageRegistry.noop(), "w1", settings, stop)
    )

    await asyncio.wait_for(_until_status(session_factory, run_id, RunStatus.COMPLETED), 30)
    stop.set()
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.db
async def test_run_worker_survives_iteration_error(
    session_factory: SessionFactory,
    clean_db: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_logging("INFO")
    run_id = await _new_run(session_factory)
    real_work_once = worker_module.work_once
    calls = 0

    async def flaky_work_once(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError(
                "SELECT bí mật", {"p": "tham số bí mật"}, Exception("mất kết nối")
            )
        return await real_work_once(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_module, "work_once", flaky_work_once)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(session_factory, StageRegistry.noop(), "w1", pipeline_settings(), stop)
    )
    await asyncio.wait_for(_until_status(session_factory, run_id, RunStatus.COMPLETED), 30)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    err = capsys.readouterr().err
    assert "bí mật" not in err and "mất kết nối" not in err
    failures = [r for r in _log_records(err) if r["event"] == "worker_iteration_failed"]
    assert len(failures) == 1
    assert (failures[0]["error_type"], failures[0]["worker_id"]) == ("OperationalError", "w1")


async def test_run_worker_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancelled_work_once(*args: object, **kwargs: object) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_module, "work_once", cancelled_work_once)
    with pytest.raises(asyncio.CancelledError):
        await run_worker(
            async_sessionmaker(), StageRegistry.noop(), "w1", pipeline_settings(), asyncio.Event()
        )
