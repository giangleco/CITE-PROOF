"""Nút "Kiểm chứng lại các tài liệu đã tải" và "Tra lại các tài liệu lỗi kết nối" (T-505)."""

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ParseMethod,
    ReferenceStatus,
    RefType,
    RunStatus,
    UnverifiableReason,
)
from citeproof.core.settings import Settings
from citeproof.db.models import ReferenceCheck, ReferenceEntry, User, VerificationRun
from citeproof.services import sources
from citeproof.storage import LocalStorage
from citeproof.web import labels
from tests.files import titled_pdf
from tests.services.helpers import add_claim_verification, new_run, run_document_id, set_run
from tests.web.conftest import login, page_csrf

SessionFactory = async_sessionmaker[AsyncSession]
TITLE = "Attention Is All You Need"


async def _run(
    session_factory: SessionFactory,
    settings: Settings,
    user: User,
    *,
    upload: bool = False,
    lookup_failed: bool = False,
) -> uuid.UUID:
    async with session_factory() as s:
        user = await s.merge(user)
        run_id = await new_run(s, settings, user)
        entry = ReferenceEntry(
            document_id=await run_document_id(s, run_id),
            order_index=0,
            raw_text="[1] Vaswani et al.",
            parsed={"title": TITLE},
            ref_type=RefType.ARTICLE.value,
            parse_method=ParseMethod.RULE.value,
        )
        s.add(entry)
        await s.flush()
        await add_claim_verification(
            s,
            run_id,
            entry_id=entry.id,
            sentence="Một nhận định chờ toàn văn [1].",
            reason=UnverifiableReason.NO_ACCESSIBLE_TEXT,
        )
        s.add(
            ReferenceCheck(
                run_id=run_id,
                reference_entry_id=entry.id,
                status=(
                    ReferenceStatus.LOOKUP_FAILED
                    if lookup_failed
                    else ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
                ).value,
                sources_searched=[] if lookup_failed else ["crossref"],
            )
        )
        await s.commit()
        await set_run(s, run_id, status=RunStatus.COMPLETED)
        if upload:
            await sources.upload_source(
                s,
                LocalStorage(settings.storage_dir),
                settings,
                user=user,
                run_id=run_id,
                reference_entry_id=entry.id,
                data=titled_pdf(TITLE),
            )
            await s.commit()
    return run_id


async def _children(session_factory: SessionFactory, run_id: uuid.UUID) -> list[uuid.UUID]:
    async with session_factory() as s:
        return list(
            (
                await s.execute(
                    select(VerificationRun.id).where(VerificationRun.parent_run_id == run_id)
                )
            ).scalars()
        )


@pytest.mark.db
async def test_reverify_button_and_redirect(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    run_id = await _run(session_factory, web_settings, user, upload=True)
    html = (await client.get(f"/runs/{run_id}/missing")).text
    assert labels.REVERIFY_BUTTON in html
    assert f'action="/runs/{run_id}/reverify"' in html

    token = await page_csrf(client, f"/runs/{run_id}/missing")
    response = await client.post(f"/runs/{run_id}/reverify", data={"csrf_token": token})
    assert response.status_code == 303
    children = await _children(session_factory, run_id)
    assert len(children) == 1
    assert response.headers["location"] == f"/runs/{children[0]}"

    # Lần chạy con đang chạy: yêu cầu thứ hai bị từ chối, không tạo thêm lần chạy.
    again = await client.post(f"/runs/{run_id}/reverify", data={"csrf_token": token})
    assert again.status_code == 409
    assert len(await _children(session_factory, run_id)) == 1


@pytest.mark.db
async def test_reverify_button_hidden_without_uploads(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    run_id = await _run(session_factory, web_settings, user)
    html = (await client.get(f"/runs/{run_id}/missing")).text
    assert labels.REVERIFY_BUTTON not in html
    token = await page_csrf(client, f"/runs/{run_id}/missing")
    response = await client.post(f"/runs/{run_id}/reverify", data={"csrf_token": token})
    assert response.status_code == 400
    assert labels.NO_NEW_UPLOADS in response.text


@pytest.mark.db
async def test_reverify_requires_csrf(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    run_id = await _run(session_factory, web_settings, user, upload=True, lookup_failed=True)
    assert (await client.post(f"/runs/{run_id}/reverify")).status_code == 403
    assert (await client.post(f"/runs/{run_id}/retry-lookups")).status_code == 403
    assert await _children(session_factory, run_id) == []


@pytest.mark.db
async def test_retry_lookups_button_and_redirect(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    run_id = await _run(session_factory, web_settings, user, lookup_failed=True)
    html = (await client.get(f"/runs/{run_id}/references")).text
    assert labels.RETRY_LOOKUPS_BUTTON in html
    assert f'action="/runs/{run_id}/retry-lookups"' in html

    token = await page_csrf(client, f"/runs/{run_id}/references")
    response = await client.post(f"/runs/{run_id}/retry-lookups", data={"csrf_token": token})
    assert response.status_code == 303
    children = await _children(session_factory, run_id)
    assert response.headers["location"] == f"/runs/{children[0]}"


@pytest.mark.db
async def test_retry_lookups_button_hidden_without_failures(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    user = await login(client, session_factory)
    run_id = await _run(session_factory, web_settings, user)
    html = (await client.get(f"/runs/{run_id}/references")).text
    assert labels.RETRY_LOOKUPS_BUTTON not in html
    token = await page_csrf(client, f"/runs/{run_id}/references")
    response = await client.post(f"/runs/{run_id}/retry-lookups", data={"csrf_token": token})
    assert response.status_code == 400
    assert labels.NO_FAILED_LOOKUPS in response.text


@pytest.mark.db
async def test_other_user_cannot_reverify(
    client: httpx.AsyncClient, session_factory: SessionFactory, web_settings: Settings
) -> None:
    from tests.web.conftest import make_account

    owner = await make_account(session_factory, email="chu@b.vn")
    run_id = await _run(session_factory, web_settings, owner, upload=True, lookup_failed=True)
    await login(client, session_factory)
    token = await page_csrf(client, "/documents")
    assert (
        await client.post(f"/runs/{run_id}/reverify", data={"csrf_token": token})
    ).status_code == 404
    assert (
        await client.post(f"/runs/{run_id}/retry-lookups", data={"csrf_token": token})
    ).status_code == 404
    assert await _children(session_factory, run_id) == []
