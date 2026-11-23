import re
from pathlib import Path

import httpx
import pytest

import citeproof.web

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)
TEMPLATES = Path(citeproof.web.__file__).parent / "templates"


def _assert_security_headers(response: httpx.Response) -> None:
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


@pytest.mark.db
async def test_security_headers_on_html_and_api(client: httpx.AsyncClient) -> None:
    _assert_security_headers(await client.get("/khong-co-trang-nay"))
    _assert_security_headers(await client.get("/api/v1/health"))


@pytest.mark.db
async def test_static_assets_served(client: httpx.AsyncClient) -> None:
    for path in (
        "/static/vendor/htmx.min.js",
        "/static/css/app.css",
        "/static/css/fonts.css",
        "/static/js/app.js",
        "/static/fonts/geist-normal-vietnamese.woff2",
    ):
        assert (await client.get(path)).status_code == 200, path


@pytest.mark.db
async def test_404_page_html(client: httpx.AsyncClient) -> None:
    response = await client.get("/khong-co-trang-nay")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Không tìm thấy trang" in response.text
    assert '<html lang="vi"' in response.text


@pytest.mark.db
async def test_api_404_stays_json(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/khong-co")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_templates_have_no_inline_script_style_or_handlers() -> None:
    files = list(TEMPLATES.rglob("*.html"))
    assert files
    for path in files:
        html = path.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path
        assert not re.search(r"\sstyle=", html), path
        assert not re.search(r"\son[a-z]+=", html), path
