import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models import Document, User
from tests.web.conftest import PASSWORD, page_csrf
from tests.web.report_helpers import sample_run

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.mark.db
async def test_delete_document_confirm_and_post(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        doc_id = (await s.execute(select(Document.id))).scalar_one()
    confirm = await client.get(f"/documents/{doc_id}/delete")
    assert confirm.status_code == 200 and "Xóa vĩnh viễn" in confirm.text
    token = await page_csrf(client, f"/runs/{run_id}/report")
    response = await client.post(f"/documents/{doc_id}/delete", data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["location"] == "/documents?done=deleted"
    listing = (await client.get(response.headers["location"])).text
    assert 'data-toast="Đã xóa bài"' in listing
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Document)) == 0


@pytest.mark.db
async def test_delete_document_requires_csrf(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await sample_run(client, session_factory)
    async with session_factory() as s:
        doc_id = (await s.execute(select(Document.id))).scalar_one()
    assert (await client.post(f"/documents/{doc_id}/delete")).status_code == 403


@pytest.mark.db
async def test_account_page_and_wrong_password(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await sample_run(client, session_factory)
    page = await client.get("/account")
    assert page.status_code == 200 and "u@b.vn" in page.text and "Xóa tài khoản" in page.text
    token = await page_csrf(client, "/account")
    response = await client.post(
        "/account/delete", data={"csrf_token": token, "password": "sai-roi-nhe"}
    )
    assert response.status_code == 400 and "Mật khẩu không đúng" in response.text


@pytest.mark.db
async def test_account_delete_logs_out(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await sample_run(client, session_factory)
    token = await page_csrf(client, "/account")
    response = await client.post(
        "/account/delete", data={"csrf_token": token, "password": PASSWORD}
    )
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert (await client.get("/documents")).status_code == 303
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0
