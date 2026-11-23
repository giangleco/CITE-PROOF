import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models import Invite, LoginAttempt, User, WebSession
from tests.db.factories import make_user

SessionFactory = async_sessionmaker[AsyncSession]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.db
async def test_session_cascades_with_user(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as s:
        user = await make_user(s)
        s.add(
            WebSession(
                token_hash=_hash("t"),
                user_id=user.id,
                csrf_token="c",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await s.commit()
        await s.execute(delete(User).where(User.id == user.id))
        await s.commit()
        assert (await s.execute(select(WebSession))).first() is None


@pytest.mark.db
async def test_invite_code_hash_unique(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as s:
        s.add(Invite(code_hash=_hash("code")))
        await s.flush()
        s.add(Invite(code_hash=_hash("code")))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_invite_survives_creator_deletion(
    session_factory: SessionFactory, clean_db: None
) -> None:
    async with session_factory() as s:
        creator = await make_user(s)
        invite = Invite(code_hash=_hash("c2"), created_by=creator.id)
        s.add(invite)
        await s.commit()
        await s.execute(delete(User).where(User.id == creator.id))
        await s.commit()
        await s.refresh(invite)
        assert invite.created_by is None


@pytest.mark.db
async def test_login_attempt_defaults(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as s:
        attempt = LoginAttempt(email_norm="a@b.c", ip="127.0.0.1", succeeded=False)
        s.add(attempt)
        await s.commit()
        await s.refresh(attempt)
        assert attempt.created_at is not None
