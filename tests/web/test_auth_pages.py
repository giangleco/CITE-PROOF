from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models import User, WebSession
from citeproof.services import auth
from tests.web.conftest import PASSWORD, csrf_token_in, login, make_account, page_csrf

SessionFactory = async_sessionmaker[AsyncSession]


async def _post_login(
    client: httpx.AsyncClient, email: str, password: str, **extra: str
) -> httpx.Response:
    token = await page_csrf(client, "/login")
    return await client.post(
        "/login", data={"email": email, "password": password, "csrf_token": token, **extra}
    )


@pytest.mark.db
async def test_login_success_sets_cookie_and_redirects(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await make_account(session_factory)
    response = await _post_login(client, "u@b.vn", PASSWORD)
    assert response.status_code == 303
    assert response.headers["location"] == "/documents"
    cookie = response.headers["set-cookie"]
    assert "cp_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


@pytest.mark.db
async def test_login_wrong_password_rerenders_form(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await make_account(session_factory)
    response = await _post_login(client, "u@b.vn", "sai-mat-khau-roi")
    assert response.status_code == 401
    assert "Email hoặc mật khẩu không đúng" in response.text
    assert "cp_session" not in response.headers.get("set-cookie", "")
    assert 'value="u@b.vn"' in response.text


@pytest.mark.db
async def test_login_rate_limited_message(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await make_account(session_factory)
    for _ in range(5):
        await _post_login(client, "u@b.vn", "sai-mat-khau-roi")
    response = await _post_login(client, "u@b.vn", PASSWORD)
    assert response.status_code == 429
    assert "Thử lại sau" in response.text


@pytest.mark.db
async def test_login_requires_csrf(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await make_account(session_factory)
    await client.get("/login")
    response = await client.post("/login", data={"email": "u@b.vn", "password": PASSWORD})
    assert response.status_code == 403


@pytest.mark.db
@pytest.mark.parametrize(
    ("next_param", "expected"),
    [
        ("//evil.example", "/documents"),
        ("/\\evil.example", "/documents"),
        ("https://evil.example", "/documents"),
        ("/runs/abc?x=1", "/runs/abc?x=1"),
    ],
)
async def test_next_param_open_redirect_blocked(
    client: httpx.AsyncClient, session_factory: SessionFactory, next_param: str, expected: str
) -> None:
    await make_account(session_factory)
    response = await _post_login(client, "u@b.vn", PASSWORD, next=next_param)
    assert response.headers["location"] == expected


@pytest.mark.db
async def test_protected_page_redirects_to_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/documents")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fdocuments"


@pytest.mark.db
async def test_htmx_request_gets_hx_redirect_when_session_expired(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    async with session_factory() as s:
        await s.execute(
            update(WebSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    response = await client.get("/documents", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/login"
    assert response.text == ""


@pytest.mark.db
async def test_consent_required_then_recorded(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory, consented=False)
    response = await client.get("/documents/new")
    assert response.status_code == 303
    assert response.headers["location"] == "/consent?next=%2Fdocuments%2Fnew"
    token = await page_csrf(client, "/consent")
    response = await client.post(
        "/consent", data={"csrf_token": token, "agree": "on", "next": "/documents/new"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/documents/new"
    async with session_factory() as s:
        user = (await s.execute(select(User))).scalar_one()
    assert user.consent_llm_at is not None


@pytest.mark.db
async def test_consent_needs_checkbox(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory, consented=False)
    token = await page_csrf(client, "/consent")
    response = await client.post("/consent", data={"csrf_token": token})
    assert response.status_code == 400
    assert "Bạn cần đồng ý" in response.text


@pytest.mark.db
async def test_consent_page_mentions_sources_and_placeholder_provider(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory, consented=False)
    html = (await client.get("/consent")).text
    for word in ("Crossref", "OpenAlex", "Semantic Scholar", "[chưa chốt nhà cung cấp]"):
        assert word in html
    assert "huấn luyện" not in html


@pytest.mark.db
async def test_consent_mentions_open_access_sources(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory, consented=False)
    html = (await client.get("/consent")).text
    assert (
        "Mã DOI và mã arXiv của tài liệu được trích được dùng để tải bản mở từ OpenAlex, "
        "Unpaywall, arXiv, ACL Anthology và trang của nhà xuất bản."
    ) in html
    for word in ("Unpaywall", "arXiv", "ACL Anthology"):
        assert word in html


@pytest.mark.db
async def test_consent_given_before_open_access_text_is_asked_again(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    user = await login(client, session_factory)
    assert (await client.get("/documents/new")).status_code == 200
    async with session_factory() as s:
        await s.execute(
            update(User)
            .where(User.id == user.id)
            .values(consent_llm_at=datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
        )
        await s.commit()
    response = await client.get("/documents/new")
    assert response.status_code == 303
    assert response.headers["location"] == "/consent?next=%2Fdocuments%2Fnew"


@pytest.mark.db
async def test_register_with_invite(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    async with session_factory() as s:
        code = await auth.create_invite(s, created_by=None)
        await s.commit()
    token = await page_csrf(client, "/register")
    form = {
        "code": code,
        "email": "moi@b.vn",
        "display_name": "Mới",
        "password": PASSWORD,
        "csrf_token": token,
    }
    response = await client.post("/register", data=form)
    assert response.status_code == 303
    assert response.headers["location"] == "/consent"
    client.cookies.clear()
    token = await page_csrf(client, "/register")
    response = await client.post(
        "/register", data={**form, "email": "khac@b.vn", "csrf_token": token}
    )
    assert response.status_code == 400
    assert "Mã mời không hợp lệ" in response.text


@pytest.mark.db
async def test_logout_deletes_session(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    await login(client, session_factory)
    old = client.cookies.get("cp_session")
    html = (await client.get("/documents")).text
    response = await client.post("/logout", data={"csrf_token": csrf_token_in(html)})
    assert response.status_code == 303
    client.cookies.set("cp_session", old or "")
    assert (await client.get("/documents")).status_code == 303


@pytest.mark.db
async def test_root_redirects(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/documents"
