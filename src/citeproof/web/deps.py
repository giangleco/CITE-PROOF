"""Dependency dùng chung cho route web: DB session, người dùng hiện tại, quyền, CSRF."""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import UserRole
from citeproof.core.errors import NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import User, WebSession
from citeproof.services import auth
from citeproof.storage import Storage
from citeproof.web.security import tokens_match

SESSION_COOKIE = "cp_session"
CSRF_COOKIE = "cp_csrf"
CSRF_HEADER = "x-csrf-token"


class LoginRequired(Exception):
    pass


class ConsentRequired(Exception):
    pass


class CsrfError(Exception):
    pass


@dataclass
class CurrentUser:
    user: User
    web_session: WebSession
    token: str

    @property
    def is_admin(self) -> bool:
        return self.user.role == UserRole.ADMIN.value


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
AppStorage = Annotated[Storage, Depends(get_storage)]


async def optional_user(request: Request, db: Db, settings: AppSettings) -> CurrentUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    loaded = await auth.load_session(db, settings, token)
    await db.commit()
    if loaded is None:
        return None
    current = CurrentUser(user=loaded[0], web_session=loaded[1], token=token)
    request.state.current = current
    return current


MaybeUser = Annotated[CurrentUser | None, Depends(optional_user)]


async def require_user(current: MaybeUser) -> CurrentUser:
    if current is None:
        raise LoginRequired
    return current


async def require_consented(
    current: Annotated[CurrentUser, Depends(require_user)], settings: AppSettings
) -> CurrentUser:
    if auth.needs_consent(current.user, settings):
        raise ConsentRequired
    return current


async def require_admin(
    current: Annotated[CurrentUser, Depends(require_consented)],
) -> CurrentUser:
    if not current.is_admin:
        raise NotFoundError()
    return current


UserDep = Annotated[CurrentUser, Depends(require_user)]
ConsentedUser = Annotated[CurrentUser, Depends(require_consented)]


def anonymous_csrf_token(request: Request) -> tuple[str, bool]:
    """Token CSRF cho người chưa đăng nhập; trả về (token, cần đặt cookie mới)."""
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return existing, False
    return secrets.token_urlsafe(32), True


async def verify_csrf(request: Request, current: MaybeUser) -> None:
    given = request.headers.get(CSRF_HEADER)
    if not given:
        form = await request.form()
        value = form.get("csrf_token")
        given = value if isinstance(value, str) else None
    expected = current.web_session.csrf_token if current else request.cookies.get(CSRF_COOKIE)
    if not tokens_match(expected, given):
        raise CsrfError


Csrf = Depends(verify_csrf)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"
