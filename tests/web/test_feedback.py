import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models import ClaimVerification, Feedback
from tests.web.conftest import page_csrf
from tests.web.report_helpers import sample_run

SessionFactory = async_sessionmaker[AsyncSession]


async def _cv(session_factory: SessionFactory) -> object:
    async with session_factory() as s:
        return (
            await s.execute(
                select(ClaimVerification.id).where(ClaimVerification.status == "CONTRADICTED")
            )
        ).scalar_one()


@pytest.mark.db
async def test_feedback_fragment_and_toast_header(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    cv = await _cv(session_factory)
    token = await page_csrf(client, f"/runs/{run_id}/claims?c={cv}")
    response = await client.post(
        f"/claim-verifications/{cv}/feedback",
        data={"verdict": "DISAGREE", "comment": "", "run_id": str(run_id)},
        headers={"HX-Request": "true", "X-CSRF-Token": token},
    )
    assert response.status_code == 200 and "<html" not in response.text
    assert 'value="DISAGREE" aria-pressed="true"' in response.text
    # Header HTTP chỉ chứa ASCII: JSON escape \uXXXX, trình duyệt giải mã lại.
    assert json.loads(response.headers["hx-trigger"]) == {"toast": "Đã lưu phản hồi"}


@pytest.mark.db
async def test_feedback_without_js_redirects_back(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    cv = await _cv(session_factory)
    token = await page_csrf(client, f"/runs/{run_id}/claims?c={cv}")
    response = await client.post(
        f"/claim-verifications/{cv}/feedback",
        data={"verdict": "AGREE", "run_id": str(run_id), "csrf_token": token},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{run_id}/claims?c={cv}"
    html = (await client.get(response.headers["location"])).text
    assert 'value="AGREE" aria-pressed="true"' in html


@pytest.mark.db
async def test_comment_markup_escaped(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    cv = await _cv(session_factory)
    token = await page_csrf(client, f"/runs/{run_id}/claims?c={cv}")
    response = await client.post(
        f"/claim-verifications/{cv}/feedback",
        data={"verdict": "UNSURE", "comment": "<b>đậm</b>", "run_id": str(run_id)},
        headers={"HX-Request": "true", "X-CSRF-Token": token},
    )
    assert "&lt;b&gt;đậm&lt;/b&gt;" in response.text and "<b>đậm" not in response.text


@pytest.mark.db
async def test_feedback_invalid_verdict_400(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    cv = await _cv(session_factory)
    token = await page_csrf(client, f"/runs/{run_id}/claims?c={cv}")
    response = await client.post(
        f"/claim-verifications/{cv}/feedback",
        data={"verdict": "MAYBE", "run_id": str(run_id), "csrf_token": token},
    )
    assert response.status_code == 400
    async with session_factory() as s:
        assert (await s.execute(select(Feedback))).first() is None
