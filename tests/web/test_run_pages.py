import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import JobStatus, RunStatus, StageName, UserRole
from citeproof.core.settings import Settings
from citeproof.db.models import User, VerificationRun
from tests.services.helpers import new_run, set_jobs, set_run
from tests.web.conftest import login, page_csrf

SessionFactory = async_sessionmaker[AsyncSession]


async def _run_for(s: AsyncSession, settings: Settings) -> object:
    user = (await s.execute(select(User))).scalar_one()
    return await new_run(s, settings, user)


@pytest.mark.db
async def test_progress_fragment_polls_only_while_active(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    await login(client, session_factory)
    async with session_factory() as s:
        run_id = await _run_for(s, web_settings)
    body = (await client.get(f"/runs/{run_id}/progress", headers={"HX-Request": "true"})).text
    assert 'hx-trigger="every 2s"' in body and "<html" not in body
    assert "Đọc tài liệu" in body
    async with session_factory() as s:
        await set_jobs(s, run_id, {st: JobStatus.DONE for st in StageName})  # type: ignore[arg-type]
        await set_run(s, run_id, status=RunStatus.COMPLETED)  # type: ignore[arg-type]
    body = (await client.get(f"/runs/{run_id}/progress", headers={"HX-Request": "true"})).text
    assert "hx-trigger" not in body and "Mở báo cáo" in body


@pytest.mark.db
async def test_run_page_shows_failure_and_resume(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    await login(client, session_factory)
    async with session_factory() as s:
        run_id = await _run_for(s, web_settings)
        await set_jobs(s, run_id, {StageName.S1: JobStatus.FAILED})  # type: ignore[arg-type]
        await set_run(
            s,
            run_id,
            status=RunStatus.FAILED,  # type: ignore[arg-type]
            error={"stage": "S1", "type": "InputError", "message": "Lỗi mô phỏng ở bước 1"},
        )
    html = (await client.get(f"/runs/{run_id}")).text
    assert "Lỗi mô phỏng ở bước 1" in html and "Tiếp tục từ bước 1" in html
    response = await client.post(
        f"/runs/{run_id}/resume", data={"csrf_token": await page_csrf(client, f"/runs/{run_id}")}
    )
    assert response.status_code == 303
    async with session_factory() as s:
        run = (await s.execute(select(VerificationRun))).scalar_one()
    assert run.status == RunStatus.RUNNING.value


@pytest.mark.db
async def test_resume_post_requires_csrf(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    await login(client, session_factory)
    async with session_factory() as s:
        run_id = await _run_for(s, web_settings)
    assert (await client.post(f"/runs/{run_id}/resume")).status_code == 403


@pytest.mark.db
async def test_tech_details_admin_only(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    await login(client, session_factory)
    async with session_factory() as s:
        run_id = await _run_for(s, web_settings)
    assert "Chi tiết kỹ thuật" not in (await client.get(f"/runs/{run_id}")).text
    client.cookies.clear()
    await login(client, session_factory, email="ad@b.vn", role=UserRole.ADMIN)
    async with session_factory() as s:
        admin = (await s.execute(select(User).where(User.email == "ad@b.vn"))).scalar_one()
        admin_run = await new_run(s, web_settings, admin)
    html = (await client.get(f"/runs/{admin_run}")).text
    assert "Chi tiết kỹ thuật" in html and "[chưa có]" in html


@pytest.mark.db
async def test_foreign_run_page_404(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    await login(client, session_factory, email="a@b.vn")
    async with session_factory() as s:
        owner = (await s.execute(select(User))).scalar_one()
        run_id = await new_run(s, web_settings, owner)
    client.cookies.clear()
    await login(client, session_factory, email="b@b.vn")
    assert (await client.get(f"/runs/{run_id}")).status_code == 404
    assert (await client.get(f"/runs/{run_id}/progress")).status_code == 404
