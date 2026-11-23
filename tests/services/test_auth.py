import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import UserRole
from citeproof.core.errors import AuthError, InputError, RateLimitedError
from citeproof.core.settings import Settings
from citeproof.db.models import Invite, User, WebSession
from citeproof.services import auth

SessionFactory = async_sessionmaker[AsyncSession]
PW = "mat-khau-du-dai"


def test_normalize_email() -> None:
    assert auth.normalize_email("  An@Example.EDU.vn ") == "an@example.edu.vn"


def test_hash_verify() -> None:
    h = auth.hash_password(PW)
    assert h.startswith("$argon2id$")
    assert auth.verify_password(h, PW)
    assert not auth.verify_password(h, "sai-mat-khau")
    assert not auth.verify_password("khong-phai-hash", PW)


@pytest.mark.db
async def test_create_user_rejects_short_password(
    session_factory: SessionFactory, clean_db: None
) -> None:
    async with session_factory() as s:
        with pytest.raises(InputError) as exc:
            await auth.create_user(s, email="a@b.vn", password="ngan", display_name="A")
    assert exc.value.code == "PASSWORD_TOO_SHORT"


@pytest.mark.db
async def test_create_user_rejects_invalid_email(
    session_factory: SessionFactory, clean_db: None
) -> None:
    async with session_factory() as s:
        with pytest.raises(InputError) as exc:
            await auth.create_user(s, email="khong-co-a-cong", password=PW, display_name="A")
    assert exc.value.code == "INVALID_EMAIL"


@pytest.mark.db
async def test_create_user_duplicate_email_case_insensitive(
    session_factory: SessionFactory, clean_db: None
) -> None:
    async with session_factory() as s:
        await auth.create_user(s, email="An@b.vn", password=PW, display_name="A")
        await s.commit()
        with pytest.raises(InputError) as exc:
            await auth.create_user(s, email="an@B.vn ", password=PW, display_name="B")
    assert exc.value.code == "EMAIL_TAKEN"


@pytest.mark.db
async def test_invite_single_use(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as s:
        code = await auth.create_invite(s, created_by=None)
        await s.commit()
        user = await auth.register_with_invite(
            s, code=code, email="x@y.vn", password=PW, display_name="X"
        )
        await s.commit()
        assert user.role == UserRole.USER.value
        with pytest.raises(InputError) as exc:
            await auth.register_with_invite(
                s, code=code, email="z@y.vn", password=PW, display_name="Z"
            )
    assert exc.value.code == "INVITE_INVALID"


@pytest.mark.db
async def test_invite_expired(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as s:
        code = await auth.create_invite(s, created_by=None, expires_in_days=0)
        await s.commit()
        with pytest.raises(InputError) as exc:
            await auth.register_with_invite(
                s, code=code, email="x@y.vn", password=PW, display_name="X"
            )
    assert exc.value.code == "INVITE_INVALID"


@pytest.mark.db
async def test_invite_stored_hashed(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as s:
        code = await auth.create_invite(s, created_by=None)
        await s.commit()
        stored = (await s.execute(select(Invite.code_hash))).scalar_one()
    assert code not in stored
    assert stored == hashlib.sha256(code.encode()).hexdigest()


async def _user(s: AsyncSession, email: str = "u@b.vn") -> User:
    user = await auth.create_user(s, email=email, password=PW, display_name="U")
    await s.commit()
    return user


@pytest.mark.db
async def test_authenticate_ok(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await _user(s)
        got = await auth.authenticate(s, settings, email="U@b.vn", password=PW, ip="1.1.1.1")
    assert got.id == user.id


@pytest.mark.db
async def test_authenticate_wrong_password_same_error_as_unknown_email(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        await _user(s)
        with pytest.raises(AuthError) as wrong:
            await auth.authenticate(s, settings, email="u@b.vn", password="sai-roi-nhe", ip="1")
        with pytest.raises(AuthError) as unknown:
            await auth.authenticate(s, settings, email="no@b.vn", password=PW, ip="1")
    assert wrong.value.code == unknown.value.code == "INVALID_CREDENTIALS"
    assert str(wrong.value) == str(unknown.value)


@pytest.mark.db
async def test_authenticate_throttles_after_five_failures(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        await _user(s)
        for _ in range(5):
            with pytest.raises(AuthError):
                await auth.authenticate(s, settings, email="u@b.vn", password="sai-roi-nhe", ip="1")
            await s.commit()
        with pytest.raises(RateLimitedError):
            await auth.authenticate(s, settings, email="u@b.vn", password=PW, ip="2")


@pytest.mark.db
async def test_success_resets_email_failures(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        await _user(s)
        for _ in range(4):
            with pytest.raises(AuthError):
                await auth.authenticate(s, settings, email="u@b.vn", password="sai-roi-nhe", ip="1")
            await s.commit()
        await auth.authenticate(s, settings, email="u@b.vn", password=PW, ip="1")
        await s.commit()
        with pytest.raises(AuthError):
            await auth.authenticate(s, settings, email="u@b.vn", password="sai-roi-nhe", ip="1")
        await s.commit()
        await auth.authenticate(s, settings, email="u@b.vn", password=PW, ip="1")


@pytest.mark.db
async def test_authenticate_throttle_per_ip(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    strict = settings.model_copy(update={"login_max_failures_per_ip": 3})
    async with session_factory() as s:
        await _user(s)
        for i in range(3):
            with pytest.raises(AuthError):
                await auth.authenticate(s, strict, email=f"n{i}@b.vn", password=PW, ip="9.9.9.9")
            await s.commit()
        with pytest.raises(RateLimitedError):
            await auth.authenticate(s, strict, email="u@b.vn", password=PW, ip="9.9.9.9")


@pytest.mark.db
async def test_session_roundtrip_and_hash_only(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await _user(s)
        token = await auth.create_session(s, settings, user_id=user.id)
        await s.commit()
        stored = (await s.execute(select(WebSession.token_hash))).scalar_one()
        assert token not in stored
        loaded = await auth.load_session(s, settings, token)
        assert loaded is not None and loaded[0].id == user.id
        assert loaded[1].csrf_token
        assert await auth.load_session(s, settings, "khong-co") is None


@pytest.mark.db
async def test_session_expired(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await _user(s)
        token = await auth.create_session(s, settings, user_id=user.id)
        await s.execute(
            update(WebSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
        assert await auth.load_session(s, settings, token) is None


@pytest.mark.db
async def test_session_slides_expiry(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await _user(s)
        token = await auth.create_session(s, settings, user_id=user.id)
        old = datetime.now(UTC) - timedelta(days=3)
        await s.execute(
            update(WebSession).values(last_seen_at=old, expires_at=old + timedelta(days=14))
        )
        await s.commit()
        loaded = await auth.load_session(s, settings, token)
        assert loaded is not None
        assert loaded[1].expires_at > datetime.now(UTC) + timedelta(days=13)


@pytest.mark.db
async def test_delete_session(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await _user(s)
        token = await auth.create_session(s, settings, user_id=user.id)
        await s.commit()
        await auth.delete_session(s, token)
        await s.commit()
        assert await auth.load_session(s, settings, token) is None


@pytest.mark.db
async def test_needs_consent(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = settings.model_copy(
        update={"consent_text_updated_at": datetime.now(UTC) - timedelta(hours=1)}
    )
    async with session_factory() as s:
        user = await _user(s)
        assert auth.needs_consent(user, settings)
        user.consent_llm_at = settings.consent_text_updated_at - timedelta(days=1)
        assert auth.needs_consent(user, settings)
        await auth.record_consent(s, user)
        await s.commit()
        await s.refresh(user)
        assert not auth.needs_consent(user, settings)
