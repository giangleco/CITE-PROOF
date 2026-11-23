"""Fixture cho test giao diện web: app thật, client httpx, helper đăng nhập."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.api.app import create_app
from citeproof.core.enums import UserRole
from citeproof.core.settings import Settings
from citeproof.db.models import User
from citeproof.services import auth

PASSWORD = "mat-khau-du-dai"
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def web_settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(database_url=database_url, env="local", storage_dir=tmp_path / "storage")


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=ASGITransport(app), base_url="http://test") as c,
    ):
        yield c


@pytest.fixture
async def client(web_settings: Settings, clean_db: None) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(web_settings) as c:
        yield c


def csrf_token_in(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match, "trang không có csrf_token"
    return match.group(1)


async def make_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str = "u@b.vn",
    role: UserRole = UserRole.USER,
    consented: bool = True,
) -> User:
    async with session_factory() as s:
        user = await auth.create_user(
            s, email=email, password=PASSWORD, display_name="Minh Anh", role=role
        )
        if consented:
            user.consent_llm_at = datetime.now(UTC)
        await s.commit()
        return user


async def login(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str = "u@b.vn",
    role: UserRole = UserRole.USER,
    consented: bool = True,
) -> User:
    user = await make_account(session_factory, email=email, role=role, consented=consented)
    page = await client.get("/login")
    response = await client.post(
        "/login",
        data={"email": email, "password": PASSWORD, "csrf_token": csrf_token_in(page.text)},
    )
    assert response.status_code == 303, response.text
    return user


async def page_csrf(client: httpx.AsyncClient, path: str) -> str:
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return csrf_token_in(response.text)
