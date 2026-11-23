import re

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.settings import Settings
from citeproof.db.models import Document
from tests.files import PDF_BYTES
from tests.web.conftest import client_for, login, page_csrf

SessionFactory = async_sessionmaker[AsyncSession]


async def _upload(
    client: httpx.AsyncClient, name: str = "bai.pdf", data: bytes = PDF_BYTES
) -> httpx.Response:
    token = await page_csrf(client, "/documents/new")
    return await client.post(
        "/documents",
        data={"csrf_token": token},
        files={"file": (name, data, "application/pdf")},
    )


@pytest.mark.db
async def test_upload_flow_redirects_to_progress(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    response = await _upload(client)
    assert response.status_code == 303
    assert re.fullmatch(r"/runs/[0-9a-f-]{36}", response.headers["location"])


@pytest.mark.db
async def test_text_flow_redirects_to_progress(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    token = await page_csrf(client, "/documents/new?mode=text")
    response = await client.post(
        "/documents/text",
        data={"csrf_token": token, "title": "Chương 2", "body": "Câu [1].", "references": "[1] A."},
    )
    assert response.status_code == 303


@pytest.mark.db
async def test_upload_wrong_type_shows_form_error(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    response = await _upload(client, "a.pdf", b"MZ\x90\x00")
    assert response.status_code == 400
    assert "PDF hoặc DOCX" in response.text


@pytest.mark.db
async def test_upload_too_large_413(
    web_settings: Settings, clean_db: None, session_factory: SessionFactory
) -> None:
    small = web_settings.model_copy(update={"max_upload_bytes": 1000})
    async with client_for(small) as client:
        await login(client, session_factory)
        response = await _upload(client, data=PDF_BYTES + b"x" * 5000)
    assert response.status_code == 413
    assert "vượt quá" in response.text


@pytest.mark.db
async def test_filename_markup_escaped_in_list(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    await _upload(client, "<img src=x onerror=alert(1)>.pdf")
    html = (await client.get("/documents")).text
    assert "&lt;img src=x onerror=alert(1)&gt;.pdf" in html
    assert "<img src=x" not in html


@pytest.mark.db
async def test_documents_empty_state(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    html = (await client.get("/documents")).text
    assert "Kiểm tra bài mới" in html
    assert "Thêm bài mẫu" in html


@pytest.mark.db
async def test_other_users_document_rerun_404(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory, email="a@b.vn")
    await _upload(client)
    async with session_factory() as s:
        doc_id = (await s.execute(select(Document.id))).scalar_one()
    client.cookies.clear()
    await login(client, session_factory, email="b@b.vn")
    token = await page_csrf(client, "/documents")
    response = await client.post(f"/documents/{doc_id}/runs", data={"csrf_token": token})
    assert response.status_code == 404


@pytest.mark.db
async def test_rerun_while_active_is_conflict(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    await _upload(client)
    async with session_factory() as s:
        doc_id = (await s.execute(select(Document.id))).scalar_one()
    token = await page_csrf(client, "/documents")
    response = await client.post(f"/documents/{doc_id}/runs", data={"csrf_token": token})
    assert response.status_code == 409


@pytest.mark.db
async def test_demo_block_only_local(
    web_settings: Settings, clean_db: None, session_factory: SessionFactory
) -> None:
    async with client_for(web_settings) as client:
        await login(client, session_factory)
        assert "Chế độ thử" in (await client.get("/documents/new")).text
    pilot = web_settings.model_copy(update={"env": "pilot", "cookie_secure": False})
    async with client_for(pilot) as client:
        await login(client, session_factory, email="p@b.vn")
        html = (await client.get("/documents/new")).text
        assert "Chế độ thử" not in html
        assert "Thêm bài mẫu" not in (await client.get("/documents")).text


@pytest.mark.db
async def test_variant_select_admin_only(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    from citeproof.core.enums import UserRole

    await login(client, session_factory)
    assert 'name="variant"' not in (await client.get("/documents/new")).text
    client.cookies.clear()
    await login(client, session_factory, email="ad@b.vn", role=UserRole.ADMIN)
    assert 'name="variant"' in (await client.get("/documents/new")).text
