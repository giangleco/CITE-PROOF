"""Xuất báo cáo HTML in được (T-604): GET /runs/{id}/report.html."""

import html as htmllib
import re

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models import ClaimVerification, Document, Evidence
from tests.web.conftest import login
from tests.web.report_helpers import sample_run

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.mark.db
async def test_print_page_has_all_sections_and_evidence(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    response = await client.get(f"/runs/{run_id}/report.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"] == "inline"
    html = response.text
    markdown = (await client.get(f"/runs/{run_id}/report.md")).text
    title = re.search(r"^# Báo cáo kiểm tra trích dẫn: (.+)$", markdown, re.M)
    assert title is not None and title.group(1) in html
    for heading in ("Tổng quan", "Tài liệu tham khảo", "Nhận định", "Còn thiếu"):
        assert heading in html, heading
    quotes = [ln[2:] for ln in markdown.splitlines() if ln.startswith("> ")]
    assert quotes
    text = htmllib.unescape(html)
    for quote in quotes:
        assert quote in text, quote
    # Phần còn lại của báo cáo.md: gợi ý hành động, câu, giải thích, nhãn trạng thái.
    todos = markdown.split("## Gợi ý hành động", 1)[1].strip().splitlines()
    assert todos and todos[0].startswith("- ")
    prefixes = ("- Câu trong bài: ", "- Giải thích: ", "- Trạng thái: ")
    fields = [ln for ln in markdown.splitlines() if ln.startswith(prefixes)]
    assert len(fields) >= 3
    for line in [*todos, *fields]:
        expected = line.removeprefix("- ")
        for prefix in prefixes:
            expected = expected.removeprefix(prefix.removeprefix("- "))
        assert expected.strip() and expected in text, line


@pytest.mark.db
async def test_print_page_is_static_and_styled_for_print(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/report.html")).text
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<script" not in html and "hx-" not in html
    # CSP là style-src 'self': kiểu in nằm trong tệp tĩnh, không inline.
    assert "<style" not in html
    href = re.search(r'<link rel="stylesheet" href="(/static/css/[^"]*print[^"]*)"', html)
    assert href is not None
    css = await client.get(href.group(1))
    assert css.status_code == 200 and "@media print" in css.text


@pytest.mark.db
async def test_print_page_authorization(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory, email="a@b.vn")
    client.cookies.clear()
    anonymous = await client.get(f"/runs/{run_id}/report.html")
    assert anonymous.status_code in (302, 303, 307)
    assert anonymous.headers["location"].startswith("/login")
    await login(client, session_factory, email="b@b.vn")
    assert (await client.get(f"/runs/{run_id}/report.html")).status_code == 404


@pytest.mark.db
async def test_print_page_escapes_user_content(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        await s.execute(update(Document).values(filename="<b>đậm</b>.docx"))
        await s.commit()
    html = (await client.get(f"/runs/{run_id}/report.html")).text
    assert "&lt;b&gt;đậm&lt;/b&gt;" in html and "<b>đậm</b>" not in html
    async with session_factory() as s:
        await s.execute(update(ClaimVerification).values(explanation="<script>x()</script> giải"))
        await s.execute(update(Evidence).values(quote_text="<b>trích</b> <script>y()</script>"))
        await s.commit()
    html = (await client.get(f"/runs/{run_id}/report.html")).text
    assert "<script" not in html and "<b>trích" not in html
    assert "&lt;script&gt;x()&lt;/script&gt;" in html and "&lt;b&gt;trích&lt;/b&gt;" in html


@pytest.mark.db
async def test_report_page_links_to_print_version(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/report")).text
    assert f'href="/runs/{run_id}/report.html"' in html and "Tải bản in" in html
