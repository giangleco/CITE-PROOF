import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, StageName
from citeproof.core.errors import InputError
from citeproof.db.models.jobs import Job
from citeproof.pipeline.jobs import (
    backoff_delay,
    claim_next,
    describe_error,
    enqueue,
    expire_stale_jobs,
    mark_done,
    mark_failed,
    stale_exhausted,
)
from tests.db.factories import make_document, make_run


def test_backoff_delay_doubles() -> None:
    assert [backoff_delay(n, 5.0).total_seconds() for n in (1, 2, 3)] == [5.0, 10.0, 20.0]


async def _run_id(session: AsyncSession) -> uuid.UUID:
    run = await make_run(session, await make_document(session))
    return run.id


async def _get(session: AsyncSession, run_id: uuid.UUID, stage: StageName) -> Job:
    return (
        await session.execute(
            select(Job)
            .where(Job.run_id == run_id, Job.stage == stage.value)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest.mark.db
async def test_enqueue_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        assert await enqueue(session, run_id, StageName.S1, {"a": 1}) is True
        assert await enqueue(session, run_id, StageName.S1) is False
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(Job))
        assert count == 1


@pytest.mark.db
async def test_claim_respects_run_after(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(
            session, run_id, StageName.S1, run_after=datetime.now(UTC) + timedelta(hours=1)
        )
        await session.commit()
        assert await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3) is None


@pytest.mark.db
async def test_claim_marks_running_and_returns_payload(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1, {"k": "v"})
        await session.commit()
        claimed = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        await session.commit()
        assert claimed is not None
        assert (claimed.run_id, claimed.stage, claimed.attempts) == (run_id, StageName.S1, 1)
        assert claimed.payload == {"k": "v"}
        job = await _get(session, run_id, StageName.S1)
        assert (job.status, job.locked_by) == (JobStatus.RUNNING.value, "w1")
        assert job.locked_at is not None
        assert claimed.locked_at == job.locked_at


@pytest.mark.db
async def test_two_sessions_claim_different_jobs(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as setup:
        run_id = await _run_id(setup)
        await enqueue(setup, run_id, StageName.S1)
        await enqueue(setup, run_id, StageName.S2)
        await setup.commit()
    async with session_factory() as s1, session_factory() as s2:
        first = await claim_next(s1, "w1", timedelta(minutes=15), max_attempts=3)
        second = await claim_next(s2, "w2", timedelta(minutes=15), max_attempts=3)
        assert first is not None and second is not None
        assert first.id != second.id
        assert await claim_next(s2, "w2", timedelta(minutes=15), max_attempts=3) is None
        await s1.commit()
        await s2.commit()


@pytest.mark.db
async def test_stale_running_job_is_reclaimed(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        first = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert first is not None
        # Còn hạn khóa: chưa được claim lại.
        assert await claim_next(session, "w2", timedelta(minutes=15), max_attempts=3) is None
        await session.execute(
            update(Job).where(Job.id == first.id).values(locked_at=func.now() - timedelta(hours=1))
        )
        await session.commit()
        again = await claim_next(session, "w2", timedelta(minutes=15), max_attempts=3)
        await session.commit()
        assert again is not None
        assert (again.id, again.attempts) == (first.id, 2)
        job = await _get(session, run_id, StageName.S1)
        assert job.locked_by == "w2"


@pytest.mark.db
async def test_mark_done(session_factory: async_sessionmaker[AsyncSession], clean_db: None) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        claimed = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert claimed is not None
        assert await mark_done(session, claimed) is True
        await session.commit()
        job = await _get(session, run_id, StageName.S1)
        assert job.status == JobStatus.DONE.value
        assert job.locked_at is None


@pytest.mark.db
async def test_retryable_failure_reschedules_then_fails(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        claimed = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert claimed is not None
        status = await mark_failed(
            session,
            claimed,
            RuntimeError("boom"),
            retryable=True,
            max_attempts=2,
            backoff_base_seconds=5.0,
        )
        assert status == JobStatus.QUEUED
        job = await _get(session, run_id, StageName.S1)
        assert job.status == JobStatus.QUEUED.value
        assert job.last_error == describe_error(RuntimeError("boom")) == "RuntimeError"
        assert job.locked_at is None and job.locked_by is None
        delta = (
            await session.scalar(select(Job.run_after - func.now()).where(Job.id == claimed.id))
        ) or timedelta(0)
        assert timedelta(seconds=3) < delta <= timedelta(seconds=5)

        await session.execute(update(Job).where(Job.id == claimed.id).values(run_after=func.now()))
        again = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert again is not None and again.attempts == 2
        status = await mark_failed(
            session,
            again,
            RuntimeError("boom"),
            retryable=True,
            max_attempts=2,
            backoff_base_seconds=5.0,
        )
        assert status == JobStatus.FAILED
        await session.commit()


@pytest.mark.db
async def test_non_retryable_failure_fails_immediately(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        claimed = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert claimed is not None
        status = await mark_failed(
            session,
            claimed,
            ValueError("secret"),
            retryable=False,
            max_attempts=5,
            backoff_base_seconds=5.0,
        )
        assert status == JobStatus.FAILED
        job = await _get(session, run_id, StageName.S1)
        assert job.last_error == "ValueError"
        assert job.locked_at is None and job.locked_by is None


def test_describe_error_only_exposes_citeproof_messages() -> None:
    assert describe_error(ValueError("bí mật")) == "ValueError"
    assert describe_error(InputError("boom")) == "InputError: boom"
    assert describe_error(InputError("x" * 900)) == "InputError: " + "x" * 500


async def _make_stale(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        update(Job).where(Job.id == job_id).values(locked_at=func.now() - timedelta(hours=1))
    )
    await session.commit()


@pytest.mark.db
async def test_exhausted_stale_job_is_expired_not_reclaimed(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        job = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=1)
        assert job is not None
        await _make_stale(session, job.id)
        stale_locked_at = (await _get(session, run_id, StageName.S1)).locked_at
        assert await claim_next(session, "w2", timedelta(minutes=15), max_attempts=1) is None
        expired = await expire_stale_jobs(session, timedelta(minutes=15), 1)
        await session.commit()
        assert [e.id for e in expired] == [job.id]
        assert expired[0].locked_at == stale_locked_at
        row = await _get(session, run_id, StageName.S1)
        assert row.status == JobStatus.FAILED.value
        assert row.last_error == "LeaseExpired"
        assert row.locked_at is None and row.locked_by is None
        assert await expire_stale_jobs(session, timedelta(minutes=15), 1) == []


@pytest.mark.db
async def test_non_exhausted_stale_job_not_expired(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        job = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert job is not None
        await _make_stale(session, job.id)
        assert await expire_stale_jobs(session, timedelta(minutes=15), 3) == []


@pytest.mark.db
async def test_stale_worker_writes_are_fenced(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        old = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert old is not None
        await _make_stale(session, old.id)
        new = await claim_next(session, "w2", timedelta(minutes=15), max_attempts=3)
        assert new is not None and new.attempts == 2
        assert new.locked_at != old.locked_at
        assert await mark_done(session, old) is False
        status = await mark_failed(
            session,
            old,
            RuntimeError("late"),
            retryable=True,
            max_attempts=3,
            backoff_base_seconds=5.0,
        )
        assert status is None
        await session.commit()
        row = await _get(session, run_id, StageName.S1)
        assert (row.status, row.locked_by, row.attempts) == (JobStatus.RUNNING.value, "w2", 2)
        assert await mark_done(session, new) is True


@pytest.mark.db
async def test_fence_uses_lease_token_not_attempts(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        old = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert old is not None
        # Giống resume_run: đưa job về QUEUED với attempts=0 rồi nhận lại.
        await session.execute(
            update(Job)
            .where(Job.id == old.id)
            .values(status=JobStatus.QUEUED.value, attempts=0, locked_at=None, locked_by=None)
        )
        await session.commit()
        new = await claim_next(session, "w2", timedelta(minutes=15), max_attempts=3)
        await session.commit()
        assert new is not None and (new.id, new.attempts) == (old.id, old.attempts)
        assert await mark_done(session, old) is False
        status = await mark_failed(
            session,
            old,
            RuntimeError("late"),
            retryable=False,
            max_attempts=3,
            backoff_base_seconds=5.0,
        )
        assert status is None
        assert await mark_done(session, new) is True
        await session.commit()


@pytest.mark.db
async def test_stale_exhausted_matches_only_expired_leases(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        for stage in (StageName.S1, StageName.S2, StageName.S3):
            await enqueue(session, run_id, stage)
            assert await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        # S1: quá hạn và hết lượt; S2: quá hạn còn lượt; S3: hết lượt nhưng còn hạn.
        await session.execute(
            update(Job).where(Job.run_id == run_id, Job.stage.in_(["S1", "S3"])).values(attempts=3)
        )
        await session.execute(
            update(Job)
            .where(Job.run_id == run_id, Job.stage.in_(["S1", "S2"]))
            .values(locked_at=func.now() - timedelta(hours=1))
        )
        matched = (
            await session.scalars(
                select(Job.stage).where(stale_exhausted(timedelta(minutes=15), 3))
            )
        ).all()
        assert matched == ["S1"]
