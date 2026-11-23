"""Test tái hiện các phát hiện của review cuối nhánh web UI (mỗi test đỏ trước khi sửa)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import citeproof.web
from citeproof.core.enums import JobStatus, RunStatus, StageName
from citeproof.core.settings import Settings
from citeproof.db.models import ClaimVerification, Document, LoginAttempt, User, WebSession
from tests.files import PDF_BYTES
from tests.services.helpers import set_jobs, set_run
from tests.web.conftest import client_for, login, make_account, page_csrf
from tests.web.report_helpers import sample_run

SessionFactory = async_sessionmaker[AsyncSession]


async def _upload(client: httpx.AsyncClient, name: str = "a.pdf") -> httpx.Response:
    token = await page_csrf(client, "/documents/new")
    return await client.post(
        "/documents",
        data={"csrf_token": token},
        files={"file": (name, PDF_BYTES, "application/pdf")},
    )


# Important 1: IP thật lấy từ X-Forwarded-For chỉ khi proxy được tin cậy.
@pytest.mark.db
async def test_client_ip_from_trusted_proxy_only(
    web_settings: Settings, clean_db: None, session_factory: SessionFactory
) -> None:
    trusted = web_settings.model_copy(update={"trusted_proxies": "127.0.0.1"})
    async with client_for(trusted) as client:
        await make_account(session_factory)
        token = await page_csrf(client, "/login")
        await client.post(
            "/login",
            data={"email": "u@b.vn", "password": "sai-mat-khau-roi", "csrf_token": token},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
    untrusted = web_settings.model_copy(update={"trusted_proxies": ""})
    async with client_for(untrusted) as client:
        token = await page_csrf(client, "/login")
        await client.post(
            "/login",
            data={"email": "u@b.vn", "password": "sai-mat-khau-roi", "csrf_token": token},
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
    async with session_factory() as s:
        ips = (
            (await s.execute(select(LoginAttempt.ip).order_by(LoginAttempt.created_at)))
            .scalars()
            .all()
        )
    assert ips == ["9.9.9.9", "127.0.0.1"]


# Important 2: xóa bài không trả lại hạn mức.
@pytest.mark.db
async def test_quota_survives_document_deletion(
    web_settings: Settings, clean_db: None, session_factory: SessionFactory
) -> None:
    tight = web_settings.model_copy(update={"runs_per_day": 1})
    async with client_for(tight) as client:
        await login(client, session_factory)
        assert (await _upload(client)).status_code == 303
        async with session_factory() as s:
            doc_id = (await s.execute(select(Document.id))).scalar_one()
        token = await page_csrf(client, "/documents")
        await client.post(f"/documents/{doc_id}/delete", data={"csrf_token": token})
        response = await _upload(client)
    assert response.status_code == 429


# Important 3: bài chưa xong vẫn xóa được và kiểm tra lại được.
@pytest.mark.db
async def test_failed_run_page_offers_delete_and_rerun(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    location = (await _upload(client)).headers["location"]
    run_id = location.split("/")[2]
    async with session_factory() as s:
        doc_id = (await s.execute(select(Document.id))).scalar_one()
        await set_jobs(s, run_id, {StageName.S1: JobStatus.FAILED})  # type: ignore[arg-type]
        await set_run(s, run_id, status=RunStatus.FAILED, error={"stage": "S1", "type": "X"})  # type: ignore[arg-type]
    html = (await client.get(location)).text
    assert f'href="/documents/{doc_id}/delete"' in html and 'value="rerun"' in html
    listing = (await client.get("/documents")).text
    assert f'href="/documents/{doc_id}/delete"' in listing


# Important 4: DOI dài trong bảng trường lệch không làm tràn ngang.
def test_diff_columns_can_shrink_and_wrap() -> None:
    css = (Path(citeproof.web.__file__).parent / "static/css/app.css").read_text()
    assert "grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr)" in css
    assert ".diff del, .diff ins { overflow-wrap: anywhere; }" in css


# Important 5: POST HTMX sau khi phiên hết hạn nhận HX-Redirect, không phải trang 403.
@pytest.mark.db
async def test_htmx_post_after_expiry_redirects_to_login(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        cv = (await s.execute(select(ClaimVerification.id))).scalars().first()
    token = await page_csrf(client, f"/runs/{run_id}/claims")
    async with session_factory() as s:
        await s.execute(
            update(WebSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    response = await client.post(
        f"/claim-verifications/{cv}/feedback",
        data={"verdict": "AGREE", "run_id": str(run_id)},
        headers={"HX-Request": "true", "X-CSRF-Token": token},
    )
    assert response.status_code == 200 and response.headers["hx-redirect"] == "/login"
    plain = await client.post(
        f"/claim-verifications/{cv}/feedback", data={"verdict": "AGREE", "csrf_token": token}
    )
    assert plain.status_code == 303 and plain.headers["location"] == "/login"


# Minor 2 (nâng lên): 405 có trang riêng; chuyển hướng đồng ý không giữ `next` của POST.
@pytest.mark.db
async def test_method_not_allowed_page(client: httpx.AsyncClient) -> None:
    response = await client.get("/logout")
    assert response.status_code == 405 and "Thao tác không hợp lệ" in response.text


@pytest.mark.db
async def test_consent_redirect_from_post_has_no_next(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory, consented=False)
    token = await page_csrf(client, "/documents")
    import uuid

    response = await client.post(f"/documents/{uuid.uuid4()}/runs", data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["location"] == "/consent"


# Minor 4 (nâng lên): tiêu đề đoạn văn giữ nguyên dấu "/".
@pytest.mark.db
async def test_text_title_keeps_slash(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    token = await page_csrf(client, "/documents/new?mode=text")
    await client.post(
        "/documents/text",
        data={
            "csrf_token": token,
            "title": "Chương 2/3: Tổng quan",
            "body": "Câu [1].",
            "references": "[1] A.",
        },
    )
    async with session_factory() as s:
        name = (await s.execute(select(Document.filename))).scalar_one()
    assert name == "Chương 2/3: Tổng quan"


# Minor 11 (nâng lên): tiêu đề trang tiến độ nằm trong mảnh tự cập nhật.
@pytest.mark.db
async def test_progress_fragment_carries_heading(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    run_id = (await _upload(client)).headers["location"].split("/")[2]
    async with session_factory() as s:
        await set_run(s, run_id, status=RunStatus.COMPLETED)  # type: ignore[arg-type]
    fragment = (await client.get(f"/runs/{run_id}/progress", headers={"HX-Request": "true"})).text
    assert "<h1>Kiểm tra xong</h1>" in fragment


# Minor 12 (nâng lên): ngày hiển thị theo giờ Việt Nam.
@pytest.mark.db
async def test_dates_shown_in_vietnam_time(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    await _upload(client)
    async with session_factory() as s:
        await s.execute(
            update(Document).values(created_at=datetime(2026, 11, 23, 20, 0, tzinfo=UTC))
        )
        await s.commit()
    assert "24/11/2026" in (await client.get("/documents")).text


# Minor 10 (nâng lên): bấm "Tiếp tục" hai lần không ra trang lỗi.
@pytest.mark.db
async def test_double_resume_redirects(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    run_id = (await _upload(client)).headers["location"].split("/")[2]
    async with session_factory() as s:
        await set_jobs(s, run_id, {StageName.S1: JobStatus.FAILED})  # type: ignore[arg-type]
        await set_run(s, run_id, status=RunStatus.FAILED, error={"stage": "S1", "type": "X"})  # type: ignore[arg-type]
    token = await page_csrf(client, f"/runs/{run_id}")
    first = await client.post(f"/runs/{run_id}/resume", data={"csrf_token": token})
    second = await client.post(f"/runs/{run_id}/resume", data={"csrf_token": token})
    assert first.status_code == 303 and second.status_code == 303
    assert second.headers["location"] == f"/runs/{run_id}"
    assert User  # dùng import


# Phát hiện thêm khi sửa: rollback làm hết hạn current.user, render trang lỗi bị sập.
@pytest.mark.db
async def test_feedback_comment_too_long_htmx_shows_error(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        cv = (await s.execute(select(ClaimVerification.id))).scalars().first()
    token = await page_csrf(client, f"/runs/{run_id}/claims")
    response = await client.post(
        f"/claim-verifications/{cv}/feedback",
        data={"verdict": "AGREE", "comment": "x" * 2001, "run_id": str(run_id)},
        headers={"HX-Request": "true", "X-CSRF-Token": token},
    )
    assert response.status_code == 200 and "Ghi chú tối đa 2000 ký tự." in response.text
