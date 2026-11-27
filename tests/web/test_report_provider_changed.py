"""Trang báo cáo và trang tiến độ của lần chạy có provider LLM khác provider hiện tại (§5.4)."""

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    JobStatus,
    ParseMethod,
    ReferenceStatus,
    RefType,
    RunStatus,
    StageName,
    UnverifiableReason,
)
from citeproof.core.settings import Settings
from citeproof.db.models import Job, ReferenceCheck, ReferenceEntry, User, VerificationRun
from citeproof.services import sources
from citeproof.storage import LocalStorage
from citeproof.web import labels
from tests.files import titled_pdf
from tests.services.helpers import (
    add_claim_verification,
    new_run,
    run_document_id,
    set_jobs,
    set_run,
)
from tests.web.conftest import login, page_csrf

SessionFactory = async_sessionmaker[AsyncSession]
TITLE = "Attention Is All You Need"
OLD_PROVIDER = "openai_compatible:old.example"


async def _run(
    session_factory: SessionFactory, settings: Settings, user: User
) -> tuple[uuid.UUID, uuid.UUID]:
    """Lần chạy đã xong: [1] lỗi tra cứu và đã tải toàn văn, [2] còn chờ toàn văn."""
    async with session_factory() as s:
        user = await s.merge(user)
        run_id = await new_run(s, settings, user)
        document_id = await run_document_id(s, run_id)
        entries = [
            ReferenceEntry(
                document_id=document_id,
                order_index=index,
                raw_text=f"[{index + 1}] Tác giả {index + 1}.",
                parsed={"title": TITLE},
                ref_type=RefType.ARTICLE.value,
                parse_method=ParseMethod.RULE.value,
            )
            for index in range(2)
        ]
        s.add_all(entries)
        await s.flush()
        for entry in entries:
            await add_claim_verification(
                s,
                run_id,
                entry_id=entry.id,
                sentence=f"Một nhận định chờ toàn văn [{entry.order_index + 1}].",
                reason=UnverifiableReason.NO_ACCESSIBLE_TEXT,
            )
        s.add(
            ReferenceCheck(
                run_id=run_id,
                reference_entry_id=entries[0].id,
                status=ReferenceStatus.LOOKUP_FAILED.value,
                sources_searched=[],
            )
        )
        await s.commit()
        await set_run(s, run_id, status=RunStatus.COMPLETED)
        await sources.upload_source(
            s,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            run_id=run_id,
            reference_entry_id=entries[0].id,
            data=titled_pdf(TITLE),
        )
        await s.commit()
    return run_id, entries[1].id


async def _freeze(session_factory: SessionFactory, run_id: uuid.UUID, provider: str) -> None:
    async with session_factory() as s:
        run = await s.get(VerificationRun, run_id)
        assert run is not None
        run.config = {**run.config, "provider": provider}
        await s.commit()


@pytest.mark.db
async def test_report_hides_actions_when_provider_changed(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    run_id, waiting_entry = await _run(session_factory, web_settings, user)
    upload_action = f'action="/runs/{run_id}/missing/{waiting_entry}/upload"'

    # Cùng provider: các hành động hiện như cũ.
    missing = (await client.get(f"/runs/{run_id}/missing")).text
    references = (await client.get(f"/runs/{run_id}/references")).text
    assert labels.REVERIFY_BUTTON in missing
    assert upload_action in missing
    assert labels.RETRY_LOOKUPS_BUTTON in references
    assert labels.LLM_PROVIDER_CHANGED not in missing

    await _freeze(session_factory, run_id, OLD_PROVIDER)
    missing = (await client.get(f"/runs/{run_id}/missing")).text
    references = (await client.get(f"/runs/{run_id}/references")).text
    assert labels.REVERIFY_BUTTON not in missing
    assert upload_action not in missing
    assert labels.RETRY_LOOKUPS_BUTTON not in references
    assert labels.LLM_PROVIDER_CHANGED in missing
    assert labels.LLM_PROVIDER_CHANGED in references

    # Báo cáo vẫn xem và xuất được.
    assert (await client.get(f"/runs/{run_id}/report")).status_code == 200
    export = await client.get(f"/runs/{run_id}/report.md")
    assert export.status_code == 200

    # Gửi thẳng yêu cầu (ví dụ từ trang cũ còn mở): bị chặn, không tạo lần chạy con.
    token = await page_csrf(client, f"/runs/{run_id}/missing")
    for path in (f"/runs/{run_id}/reverify", f"/runs/{run_id}/retry-lookups"):
        response = await client.post(path, data={"csrf_token": token})
        assert response.status_code == 409
        assert labels.LLM_PROVIDER_CHANGED in response.text
    upload = await client.post(
        f"/runs/{run_id}/missing/{waiting_entry}/upload",
        data={"csrf_token": token},
        files={"file": ("a.pdf", titled_pdf(TITLE), "application/pdf")},
    )
    assert upload.status_code == 409
    assert labels.LLM_PROVIDER_CHANGED in upload.text


@pytest.mark.db
async def test_resume_blocked_when_provider_changed(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    async with session_factory() as s:
        user = await s.merge(user)
        run_id = await new_run(s, web_settings, user)
        await set_jobs(s, run_id, {StageName.S5: JobStatus.FAILED})
        await set_run(
            s,
            run_id,
            status=RunStatus.FAILED,
            error={"stage": "S5", "type": "InputError", "message": "Lỗi mô phỏng ở bước 5"},
        )
    html = (await client.get(f"/runs/{run_id}")).text
    assert "Tiếp tục từ bước 5" in html
    assert labels.LLM_PROVIDER_CHANGED not in html

    await _freeze(session_factory, run_id, OLD_PROVIDER)
    for path in (f"/runs/{run_id}", f"/runs/{run_id}/progress"):
        body = (await client.get(path)).text
        assert "Tiếp tục từ bước" not in body
        assert f'action="/runs/{run_id}/resume"' not in body
        assert labels.LLM_PROVIDER_CHANGED in body
        assert "Kiểm tra lại cả bài" in body

    token = await page_csrf(client, f"/runs/{run_id}")
    response = await client.post(f"/runs/{run_id}/resume", data={"csrf_token": token})
    assert response.status_code == 409
    assert labels.LLM_PROVIDER_CHANGED in response.text
    async with session_factory() as s:
        run = await s.get(VerificationRun, run_id)
        job_status = await s.scalar(
            select(Job.status).where(Job.run_id == run_id, Job.stage == StageName.S5.value)
        )
    assert run is not None
    assert run.status == RunStatus.FAILED.value
    assert job_status == JobStatus.FAILED.value
