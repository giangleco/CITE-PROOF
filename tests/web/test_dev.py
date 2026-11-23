import re

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.settings import Settings
from tests.web.conftest import client_for, login, page_csrf

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.mark.db
async def test_dev_sample_route_local_only(
    web_settings: Settings, clean_db: None, session_factory: SessionFactory
) -> None:
    async with client_for(web_settings) as client:
        await login(client, session_factory)
        token = await page_csrf(client, "/documents")
        response = await client.post("/dev/sample", data={"csrf_token": token})
        assert response.status_code == 303
        assert re.fullmatch(r"/runs/[0-9a-f-]{36}/report", response.headers["location"])
        assert "Bài mẫu" in (await client.get("/documents")).text
    pilot = web_settings.model_copy(update={"env": "pilot", "cookie_secure": False})
    async with client_for(pilot) as client:
        await login(client, session_factory, email="p@b.vn")
        token = await page_csrf(client, "/documents")
        assert (await client.post("/dev/sample", data={"csrf_token": token})).status_code == 404


@pytest.mark.db
async def test_sample_document_cannot_be_rerun(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    token = await page_csrf(client, "/documents")
    location = (await client.post("/dev/sample", data={"csrf_token": token})).headers["location"]
    from sqlalchemy import select

    from citeproof.db.models import Document

    async with session_factory() as s:
        doc_id = (await s.execute(select(Document.id))).scalar_one()
    response = await client.post(f"/documents/{doc_id}/runs", data={"csrf_token": token})
    assert response.status_code == 400
    assert "Bài mẫu không kiểm tra lại được" in response.text
    assert location
