"""Đăng nhập và tạo bài mẫu qua giao diện, trả về run_id."""

from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import UserRole
from tests.web.conftest import login, page_csrf


async def sample_run(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str = "u@b.vn",
    role: UserRole = UserRole.USER,
) -> UUID:
    await login(client, session_factory, email=email, role=role)
    token = await page_csrf(client, "/documents")
    location = (await client.post("/dev/sample", data={"csrf_token": token})).headers["location"]
    return UUID(location.split("/")[2])
