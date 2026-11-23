import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, RunStatus, StageName
from citeproof.core.errors import InputError, RetryableError
from citeproof.core.logging import ALLOWED_LOG_FIELDS, configure_logging
from citeproof.db.models import Job, VerificationRun
from citeproof.pipeline.jobs import ClaimedJob, claim_next, enqueue
from citeproof.pipeline.runs import complete_stage, create_run, fail_stage, resume_run
from citeproof.pipeline.stages import STAGE_DEPENDENCIES, NoopStage, StageRegistry, StageResult
from citeproof.pipeline.worker import run_worker, work_once
from tests.db.factories import make_document
from tests.pipeline.helpers import (
    CountingStage,
    FailingStage,
    counting_registry,
    drain,
    pipeline_settings,
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
    assert await NoopStage(S.S3).run(uuid.uuid4()) == StageResult()


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
    assert len(stages[S.S2].calls) == 2


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
    """Chỉ trả kết quả khi đủ số stage cùng chờ ở barrier (buộc chạy song song)."""

    def __init__(self, name: StageName, barrier: asyncio.Barrier) -> None:
        super().__init__(name)
        self.barrier = barrier

    async def run(self, run_id: uuid.UUID) -> StageResult:
        result = await super().run(run_id)
        await self.barrier.wait()
        return result


@pytest.mark.db
async def test_concurrent_workers_enqueue_s5_once(
    session_factory: SessionFactory, clean_db: None
) -> None:
    settings = pipeline_settings()
    barrier = asyncio.Barrier(2)
    registry, stages = counting_registry(BarrierStage(S.S3, barrier), BarrierStage(S.S4, barrier))
    run_id = await _new_run(session_factory)

    async def worker(worker_id: str) -> None:
        while True:
            if await work_once(session_factory, registry, worker_id, settings):
                continue
            if RunStatus((await _run(session_factory, run_id)).status) in TERMINAL:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(asyncio.gather(worker("w1"), worker("w2")), timeout=30)

    async with session_factory() as session:
        s5_rows = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.run_id == run_id, Job.stage == S.S5.value)
        )
    assert s5_rows == 1
    run = await _run(session_factory, run_id)
    assert run.status == RunStatus.COMPLETED.value
    jobs = await _jobs(session_factory, run_id)
    assert {job.status for job in jobs.values()} == {JobStatus.DONE.value}
    assert all(len(stage.calls) == 1 for stage in stages.values())


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


async def _claim_then_lose_lease(session_factory: SessionFactory) -> ClaimedJob:
    """Worker cũ nhận S1, quá hạn khóa, worker mới nhận lại; trả về lease cũ."""
    async with session_factory() as session:
        old = await claim_next(session, "w-old", timedelta(minutes=15), max_attempts=3)
        assert old is not None
        await session.execute(
            update(Job).where(Job.id == old.id).values(locked_at=func.now() - timedelta(hours=1))
        )
        await session.commit()
        new = await claim_next(session, "w-new", timedelta(minutes=15), max_attempts=3)
        await session.commit()
    assert new is not None and (new.id, new.attempts) == (old.id, 2)
    return old


@pytest.mark.db
async def test_completion_after_lost_lease_is_ignored(
    session_factory: SessionFactory, clean_db: None
) -> None:
    run_id = await _new_run(session_factory)
    old = await _claim_then_lose_lease(session_factory)
    async with session_factory() as session:
        await complete_stage(session, old, StageResult(warnings=("muộn",)))
        await session.commit()
    run = await _run(session_factory, run_id)
    assert (run.status, run.warnings) == (RunStatus.QUEUED.value, [])
    jobs = await _jobs(session_factory, run_id)
    assert list(jobs) == [S.S1]
    assert (jobs[S.S1].status, jobs[S.S1].locked_by) == (JobStatus.RUNNING.value, "w-new")


@pytest.mark.db
async def test_failure_after_lost_lease_is_ignored(
    session_factory: SessionFactory, clean_db: None
) -> None:
    run_id = await _new_run(session_factory)
    old = await _claim_then_lose_lease(session_factory)
    async with session_factory() as session:
        await fail_stage(session, old, InputError("muộn"), pipeline_settings())
        await session.commit()
    run = await _run(session_factory, run_id)
    assert (run.status, run.error, run.finished_at) == (RunStatus.QUEUED.value, None, None)
    job = (await _jobs(session_factory, run_id))[S.S1]
    assert (job.status, job.locked_by, job.last_error) == (JobStatus.RUNNING.value, "w-new", None)


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
    records = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    base_fields = {"timestamp", "level", "module", "event"}
    assert all(set(record) <= ALLOWED_LOG_FIELDS | base_fields for record in records)
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
            update(Job).where(Job.id == job.id).values(locked_at=func.now() - timedelta(hours=1))
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
    assert stages[S.S1].calls == []


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

    async def until_completed() -> None:
        while True:
            if (await _run(session_factory, run_id)).status == RunStatus.COMPLETED.value:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(until_completed(), timeout=30)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
