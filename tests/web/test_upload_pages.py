import re
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, SourceOrigin, TitleMatch
from citeproof.core.settings import Settings
from citeproof.db.models import SourceFile, VerificationRun
from citeproof.web import labels
from tests.files import PDF_BYTES, docx_bytes, titled_pdf
from tests.web.conftest import login, page_csrf
from tests.web.report_helpers import sample_run

SessionFactory = async_sessionmaker[AsyncSession]
_ACTION = re.compile(r'action="(/runs/[^"]+/missing/([0-9a-f-]{36})/upload)"')


async def _first_upload_action(client: httpx.AsyncClient, run_id: UUID) -> str:
    html = (await client.get(f"/runs/{run_id}/missing")).text
    match = _ACTION.search(html)
    assert match, html
    return match.group(1)


async def _upload(
    client: httpx.AsyncClient, run_id: UUID, action: str, data: bytes, *, name: str = "a.pdf"
) -> httpx.Response:
    token = await page_csrf(client, f"/runs/{run_id}/missing")
    return await client.post(
        action, data={"csrf_token": token}, files={"file": (name, data, "application/pdf")}
    )


async def _count(session_factory: SessionFactory) -> int:
    async with session_factory() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(SourceFile)
                .where(
                    SourceFile.origin == SourceOrigin.USER_UPLOAD.value,
                    SourceFile.storage_key.like("documents/%/sources/%"),  # bỏ nguồn của bài mẫu
                )
            )
            or 0
        )


@pytest.mark.db
async def test_upload_page_shows_title_warning(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    response = await _upload(client, run_id, action, titled_pdf("Một bài hoàn toàn khác xyz"))
    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{run_id}/missing"
    html = (await client.get(f"/runs/{run_id}/missing")).text
    assert labels.TITLE_MATCH[TitleMatch.MISMATCH] in html
    assert "Xóa tệp này" in html
    assert await _count(session_factory) == 1

    discard = re.search(r'action="(/runs/[^"]+/discard)"', html)
    assert discard
    token = await page_csrf(client, f"/runs/{run_id}/missing")
    assert (await client.post(discard.group(1), data={"csrf_token": token})).status_code == 303
    assert await _count(session_factory) == 0
    assert "Xóa tệp này" not in (await client.get(f"/runs/{run_id}/missing")).text


@pytest.mark.db
async def test_upload_requires_csrf(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    response = await client.post(action, files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
    assert response.status_code == 403
    assert await _count(session_factory) == 0


@pytest.mark.db
async def test_upload_rejects_non_pdf_with_message(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    response = await _upload(client, run_id, action, docx_bytes(), name="a.docx")
    assert response.status_code == 400
    assert "Chỉ nhận tệp PDF" in response.text
    assert await _count(session_factory) == 0


@pytest.mark.db
async def test_upload_too_large_is_413(
    web_settings: Settings, session_factory: SessionFactory, clean_db: None
) -> None:
    from tests.web.conftest import client_for

    small = web_settings.model_copy(update={"max_upload_bytes": 200})
    async with client_for(small) as client:
        run_id = await sample_run(client, session_factory)
        action = await _first_upload_action(client, run_id)
        response = await _upload(client, run_id, action, titled_pdf("x" * 50))
    assert response.status_code == 413


@pytest.mark.db
async def test_other_user_cannot_upload_or_discard(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    assert (await _upload(client, run_id, action, titled_pdf("Bài A"))).status_code == 303
    async with session_factory() as s:
        source_id = await s.scalar(
            select(SourceFile.id).where(SourceFile.storage_key.like("documents/%/sources/%"))
        )
    await client.post("/logout", data={"csrf_token": await page_csrf(client, "/documents")})
    await login(client, session_factory, email="b@b.vn")
    token = await page_csrf(client, "/documents")
    response = await client.post(
        action, data={"csrf_token": token}, files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}
    )
    assert response.status_code == 404
    discard = await client.post(
        f"/runs/{run_id}/sources/{source_id}/discard", data={"csrf_token": token}
    )
    assert discard.status_code == 404
    assert await _count(session_factory) == 1


def _stored(settings: Settings) -> Sequence[Path]:
    return list(Path(settings.storage_dir).rglob("sources/*.pdf"))


@pytest.mark.db
async def test_upload_stores_file_under_document(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    await _upload(client, run_id, action, titled_pdf("Bài A"))
    assert len(_stored(web_settings)) == 1


@pytest.mark.db
async def test_discard_in_use_source_is_409(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    from citeproof.web import labels
    from tests.services.test_sources import _add_evidence

    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    await _upload(client, run_id, action, titled_pdf("Bài A"))
    async with session_factory() as s:
        source_id = await s.scalar(
            select(SourceFile.id).where(SourceFile.storage_key.like("documents/%/sources/%"))
        )
        assert source_id is not None
        await _add_evidence(s, run_id, source_id)
    token = await page_csrf(client, f"/runs/{run_id}/missing")
    response = await client.post(
        f"/runs/{run_id}/sources/{source_id}/discard", data={"csrf_token": token}
    )
    assert response.status_code == 409
    assert labels.SOURCE_IN_USE in response.text
    assert await _count(session_factory) == 1


@pytest.mark.db
async def test_discard_while_run_active_is_409(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    action = await _first_upload_action(client, run_id)
    await _upload(client, run_id, action, titled_pdf("Bài A"))
    async with session_factory() as s:
        source_id = await s.scalar(
            select(SourceFile.id).where(SourceFile.storage_key.like("documents/%/sources/%"))
        )
        await s.execute(
            update(VerificationRun)
            .where(VerificationRun.id == run_id)
            .values(status=RunStatus.RUNNING.value)
        )
        await s.commit()
    token = await page_csrf(client, f"/runs/{run_id}/missing")
    response = await client.post(
        f"/runs/{run_id}/sources/{source_id}/discard", data={"csrf_token": token}
    )
    assert response.status_code == 409
    assert "đang được kiểm tra" in response.text
    assert await _count(session_factory) == 1
