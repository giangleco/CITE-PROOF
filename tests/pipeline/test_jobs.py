import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, StageName
from citeproof.core.errors import InputError, LeaseLostError
from citeproof.db.models.jobs import Job
from citeproof.pipeline.jobs import (
    ClaimedJob,
    backoff_delay,
    claim_next,
    describe_error,
    enqueue,
    expire_stale_jobs,
    fenced_session,
    mark_done,
    mark_failed,
    renew_lease,
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


async def _expire_lease(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Cho lease hết hạn (worker giữ job coi như đã chết) rồi commit."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(lease_expires_at=func.now() - timedelta(seconds=1))
    )
    await session.commit()


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
async def test_claim_sets_lease_and_started_at(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        await session.commit()
        claimed = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
        assert claimed is not None
        job = await _get(session, run_id, StageName.S1)
        assert job.locked_at is not None and job.lease_expires_at is not None
        lease = job.lease_expires_at - job.locked_at
        assert timedelta(seconds=119) <= lease <= timedelta(seconds=121)
        assert job.started_at == job.heartbeat_at == job.locked_at == claimed.locked_at
        assert job.finished_at is None


@pytest.mark.db
async def test_expired_lease_is_reclaimed(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        first = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        assert first is not None
        # Còn hạn lease: chưa được claim lại.
        assert await claim_next(session, "w2", timedelta(seconds=120), max_attempts=3) is None
        await _expire_lease(session, first.id)
        again = await claim_next(session, "w2", timedelta(seconds=120), max_attempts=3)
        await session.commit()
        assert again is not None
        assert (again.id, again.attempts) == (first.id, 2)
        assert again.locked_at != first.locked_at
        job = await _get(session, run_id, StageName.S1)
        assert job.locked_by == "w2"
        assert job.finished_at is None


@pytest.mark.db
async def test_renew_extends_lease(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        claimed = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
        assert claimed is not None
        before = await _get(session, run_id, StageName.S1)
        previous_expiry, previous_heartbeat = before.lease_expires_at, before.heartbeat_at
        assert previous_expiry is not None and previous_heartbeat is not None
        assert await renew_lease(session, claimed, timedelta(seconds=120)) is True
        await session.commit()
        after = await _get(session, run_id, StageName.S1)
        assert after.lease_expires_at is not None and after.lease_expires_at > previous_expiry
        assert after.heartbeat_at is not None and after.heartbeat_at > previous_heartbeat
        # Gia hạn không đổi token fencing.
        assert (after.locked_at, after.status) == (claimed.locked_at, JobStatus.RUNNING.value)


@pytest.mark.db
async def test_renew_after_reclaim_returns_false(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        old = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        assert old is not None
        await _expire_lease(session, old.id)
        new = await claim_next(session, "w2", timedelta(seconds=120), max_attempts=3)
        await session.commit()
        assert new is not None and new.id == old.id
        expiry = (await _get(session, run_id, StageName.S1)).lease_expires_at
        assert await renew_lease(session, old, timedelta(seconds=120)) is False
        await session.commit()
        row = await _get(session, run_id, StageName.S1)
        assert (row.locked_by, row.lease_expires_at) == ("w2", expiry)
        assert await renew_lease(session, new, timedelta(seconds=120)) is True


@pytest.mark.db
async def test_fenced_session_raises_after_reclaim(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        old = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
        assert old is not None
    # Lease còn giữ: ghi trong fenced_session được commit khi thoát.
    async with fenced_session(session_factory, old) as fenced:
        await fenced.execute(update(Job).where(Job.id == old.id).values(payload={"ghi": 1}))
    async with session_factory() as session:
        assert (await _get(session, run_id, StageName.S1)).payload == {"ghi": 1}
        await _expire_lease(session, old.id)
        assert await claim_next(session, "w2", timedelta(seconds=120), max_attempts=3)
        await session.commit()
    with pytest.raises(LeaseLostError):
        async with fenced_session(session_factory, old) as fenced:
            await fenced.execute(update(Job).where(Job.id == old.id).values(payload={"ghi": 2}))
    async with session_factory() as session:
        assert (await _get(session, run_id, StageName.S1)).payload == {"ghi": 1}


async def _claimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, ClaimedJob]:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        lease = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
    assert lease is not None
    return run_id, lease


@pytest.mark.db
async def test_heartbeat_not_blocked_by_open_fenced_session(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    _, lease = await _claimed(session_factory)

    async def renew() -> bool:
        async with session_factory() as session:
            renewed = await renew_lease(session, lease, timedelta(seconds=120))
            await session.commit()
            return renewed

    async with fenced_session(session_factory, lease):
        assert await asyncio.wait_for(renew(), timeout=2) is True


@pytest.mark.db
async def test_open_fenced_session_blocks_reclaim_of_expired_lease(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    _, lease = await _claimed(session_factory)
    async with session_factory() as session:
        await _expire_lease(session, lease.id)
    async with fenced_session(session_factory, lease), session_factory() as other:
        assert await claim_next(other, "w2", timedelta(seconds=120), max_attempts=3) is None
        await other.commit()
    # Giao dịch ghi đã đóng: lease hết hạn được nhận lại như thường.
    async with session_factory() as other:
        again = await claim_next(other, "w2", timedelta(seconds=120), max_attempts=3)
        await other.commit()
    assert again is not None and (again.id, again.attempts) == (lease.id, 2)


@pytest.mark.db
async def test_renew_counts_from_wall_clock_not_transaction_start(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    run_id, lease = await _claimed(session_factory)
    async with session_factory() as session:
        # Mở giao dịch: now() cố định từ đây, giống heartbeat phải chờ lâu.
        started = await session.scalar(select(func.now()))
        await asyncio.sleep(0.5)
        assert await renew_lease(session, lease, timedelta(seconds=1)) is True
        await session.commit()
        job = await _get(session, run_id, StageName.S1)
    assert started is not None and job.lease_expires_at is not None
    assert job.lease_expires_at - started >= timedelta(seconds=1.5)
    assert job.heartbeat_at is not None and job.heartbeat_at - started >= timedelta(seconds=0.5)


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
async def test_done_and_failed_record_finished_at(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        await enqueue(session, run_id, StageName.S2)
        await session.commit()
        done = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        failed = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
        assert done is not None and failed is not None
        assert await mark_done(session, done) is True
        status = await mark_failed(
            session,
            failed,
            InputError("x"),
            retryable=False,
            max_attempts=3,
            backoff_base_seconds=5.0,
        )
        assert status == JobStatus.FAILED
        await session.commit()
        for claimed in (done, failed):
            job = await _get(session, run_id, claimed.stage)
            assert job.started_at is not None and job.finished_at is not None
            assert job.finished_at > job.started_at


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


@pytest.mark.db
async def test_exhausted_stale_job_is_expired_not_reclaimed(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        job = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=1)
        assert job is not None
        await _expire_lease(session, job.id)
        stale_locked_at = (await _get(session, run_id, StageName.S1)).locked_at
        assert await claim_next(session, "w2", timedelta(minutes=15), max_attempts=1) is None
        expired = await expire_stale_jobs(session, 1)
        await session.commit()
        assert [e.id for e in expired] == [job.id]
        assert expired[0].locked_at == stale_locked_at
        row = await _get(session, run_id, StageName.S1)
        assert row.status == JobStatus.FAILED.value
        assert row.last_error == "LeaseExpired"
        assert row.locked_at is None and row.locked_by is None
        assert row.finished_at is not None
        assert await expire_stale_jobs(session, 1) == []


@pytest.mark.db
async def test_non_exhausted_stale_job_not_expired(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        job = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert job is not None
        await _expire_lease(session, job.id)
        assert await expire_stale_jobs(session, 3) == []


@pytest.mark.db
async def test_stale_worker_writes_are_fenced(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as session:
        run_id = await _run_id(session)
        await enqueue(session, run_id, StageName.S1)
        old = await claim_next(session, "w1", timedelta(minutes=15), max_attempts=3)
        assert old is not None
        await _expire_lease(session, old.id)
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
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        matched = (await session.scalars(select(Job.stage).where(stale_exhausted(3)))).all()
        assert matched == ["S1"]
