"""Tài khoản, mã mời, mật khẩu, phiên đăng nhập, giới hạn đăng nhập, đồng ý dữ liệu.

Mật khẩu băm Argon2id (`10`). Mã mời và token phiên chỉ lưu SHA-256. Không hàm nào ở
đây ghi log mật khẩu, mã mời hay token.
"""

import hashlib
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from citeproof.core.enums import UserRole
from citeproof.core.errors import AuthError, InputError, RateLimitedError
from citeproof.core.settings import Settings
from citeproof.db.models import Invite, LoginAttempt, User, WebSession
from citeproof.llm.identity import provider_identity

MIN_PASSWORD_LENGTH = 10
_SLIDE_EVERY = timedelta(minutes=1)
_ATTEMPT_RETENTION = timedelta(days=1)
_hasher = PasswordHasher()
# Băm sẵn để thời gian xử lý email không tồn tại giống email có thật.
_DUMMY_HASH = _hasher.hash("citeproof-dummy-password")

INVALID_CREDENTIALS = "Email hoặc mật khẩu không đúng."


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFC", email).strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InputError(
            f"Mật khẩu cần ít nhất {MIN_PASSWORD_LENGTH} ký tự.", code="PASSWORD_TOO_SHORT"
        )


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    role: UserRole = UserRole.USER,
) -> User:
    email_norm = normalize_email(email)
    if "@" not in email_norm or email_norm.startswith("@") or email_norm.endswith("@"):
        raise InputError("Email không hợp lệ.", code="INVALID_EMAIL")
    _check_password(password)
    exists = await session.scalar(select(User.id).where(User.email == email_norm))
    if exists is not None:
        raise InputError("Email này đã có tài khoản.", code="EMAIL_TAKEN")
    name = unicodedata.normalize("NFC", display_name).strip() or email_norm.split("@")[0]
    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=name[:100],
        role=role.value,
    )
    session.add(user)
    await session.flush()
    return user


async def create_invite(
    session: AsyncSession, *, created_by: UUID | None, expires_in_days: int | None = 14
) -> str:
    code = secrets.token_urlsafe(12)
    expires_at = None if expires_in_days is None else _now() + timedelta(days=expires_in_days)
    session.add(Invite(code_hash=_sha256(code), created_by=created_by, expires_at=expires_at))
    await session.flush()
    return code


async def register_with_invite(
    session: AsyncSession, *, code: str, email: str, password: str, display_name: str
) -> User:
    invite = (
        await session.execute(
            select(Invite).where(Invite.code_hash == _sha256(code.strip())).with_for_update()
        )
    ).scalar_one_or_none()
    if (
        invite is None
        or invite.used_at is not None
        or (invite.expires_at is not None and invite.expires_at <= _now())
    ):
        raise InputError("Mã mời không hợp lệ hoặc đã được dùng.", code="INVITE_INVALID")
    user = await create_user(session, email=email, password=password, display_name=display_name)
    invite.used_by = user.id
    invite.used_at = _now()
    await session.flush()
    return user


async def _failures(
    session: AsyncSession, column: InstrumentedAttribute[str], value: str, since: datetime
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(column == value, LoginAttempt.succeeded.is_(False), LoginAttempt.created_at > since)
    )
    return int(count or 0)


async def authenticate(
    session: AsyncSession, settings: Settings, *, email: str, password: str, ip: str
) -> User:
    """Trả về người dùng khi đúng; ghi lại mọi lần thử không bị chặn."""
    email_norm = normalize_email(email)
    now = _now()
    window_start = now - timedelta(minutes=settings.login_window_minutes)
    last_success = await session.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.email_norm == email_norm, LoginAttempt.succeeded.is_(True)
        )
    )
    email_since = max(window_start, last_success) if last_success else window_start
    if (
        await _failures(session, LoginAttempt.email_norm, email_norm, email_since)
        >= settings.login_max_failures_per_email
        or await _failures(session, LoginAttempt.ip, ip, window_start)
        >= settings.login_max_failures_per_ip
    ):
        raise RateLimitedError(
            f"Bạn đã nhập sai quá nhiều lần. Thử lại sau {settings.login_window_minutes} phút.",
            code="RATE_LIMITED",
        )

    user = (
        await session.execute(
            select(User).where(User.email == email_norm, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, password) and user is not None
    await session.execute(
        delete(LoginAttempt).where(LoginAttempt.created_at < now - _ATTEMPT_RETENTION)
    )
    session.add(LoginAttempt(email_norm=email_norm, ip=ip, succeeded=ok))
    await session.flush()
    if not ok or user is None:
        raise AuthError(INVALID_CREDENTIALS, code="INVALID_CREDENTIALS")
    return user


async def create_session(session: AsyncSession, settings: Settings, *, user_id: UUID) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    session.add(
        WebSession(
            token_hash=_sha256(token),
            user_id=user_id,
            csrf_token=secrets.token_urlsafe(32),
            last_seen_at=now,
            expires_at=now + timedelta(days=settings.session_ttl_days),
        )
    )
    await session.flush()
    return token


async def load_session(
    session: AsyncSession, settings: Settings, token: str
) -> tuple[User, WebSession] | None:
    now = _now()
    row = (
        await session.execute(
            select(User, WebSession)
            .join(WebSession, WebSession.user_id == User.id)
            .where(
                WebSession.token_hash == _sha256(token),
                WebSession.expires_at > now,
                User.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        return None
    user, web_session = row
    if now - web_session.last_seen_at >= _SLIDE_EVERY:
        web_session.last_seen_at = now
        web_session.expires_at = now + timedelta(days=settings.session_ttl_days)
        await session.flush()
    return user, web_session


async def delete_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(WebSession).where(WebSession.token_hash == _sha256(token)))


def needs_consent(user: User, settings: Settings) -> bool:
    if user.consent_llm_at is None or user.consent_llm_at < settings.consent_text_updated_at:
        return True
    current = provider_identity(settings)
    if user.consent_llm_provider is None:
        # Đồng ý từ trước khi có cột: chỉ tương đương Anthropic hoặc chưa cấu hình LLM.
        return current not in (None, "anthropic")
    return user.consent_llm_provider != current


async def record_consent(session: AsyncSession, user: User, settings: Settings) -> None:
    user.consent_llm_at = _now()
    user.consent_llm_provider = provider_identity(settings)
    await session.flush()
