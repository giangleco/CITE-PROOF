import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import ClaimStatus, UnverifiableReason, UserRole
from citeproof.db.models import ClaimVerification, ReferenceEntry
from citeproof.web.labels import (
    CLAIM_STATUS,
    FORBIDDEN_WORDS,
    REASONS,
    UNCITED_REFERENCE,
    UNRESOLVED_REF,
)
from tests.services.helpers import UNRESOLVED_SENTENCE, add_claim_verification
from tests.web.report_helpers import sample_run

SessionFactory = async_sessionmaker[AsyncSession]


def _no_forbidden(html: str) -> None:
    for word in FORBIDDEN_WORDS:
        assert word not in html.lower(), word


@pytest.mark.db
async def test_overview_page(client: httpx.AsyncClient, session_factory: SessionFactory) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/report")).text
    for status in ClaimStatus:
        assert CLAIM_STATUS[status].label in html
    assert "Nên làm tiếp" in html and "Bài mẫu" in html
    assert "Trang 41, 42, 43 có vẻ là ảnh quét" in html
    assert f'href="/runs/{run_id}/claims?flag=1"' in html
    _no_forbidden(html)


@pytest.mark.db
async def test_references_page_badges(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/references")).text
    assert 'class="st absent"' in html and 'class="st syserr"' in html
    assert "Chưa tìm thấy trong Crossref, OpenAlex, Semantic Scholar" in html
    assert "<del>2018</del>" in html and "<ins>2019</ins>" in html
    _no_forbidden(html)
    filtered = (await client.get(f"/runs/{run_id}/references?status=LOOKUP_FAILED")).text
    assert "RoBERTa" in filtered and "PhoBERT" not in filtered


@pytest.mark.db
async def test_lookup_failed_offers_rerun_except_sample(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/references?status=LOOKUP_FAILED")).text
    assert "không phải kết quả “chưa tìm thấy”" in html
    assert 'value="rerun"' not in html  # bài mẫu: không có nút kiểm tra lại


@pytest.mark.db
async def test_claims_page_default_sheet_and_htmx_fragment(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        cv = (
            await s.execute(
                select(ClaimVerification.id).where(ClaimVerification.status == "CONTRADICTED")
            )
        ).scalar_one()
    html = (await client.get(f"/runs/{run_id}/claims?c={cv}")).text
    assert "Mâu thuẫn với nguồn" in html and "BERT is designed to pre-train" in html
    assert '<blockquote class="quote CONTRADICTS" lang="en">' in html
    assert f'hx-get="/runs/{run_id}/claims/{cv}"' in html
    fragment = await client.get(f"/runs/{run_id}/claims/{cv}", headers={"HX-Request": "true"})
    assert fragment.status_code == 200 and "<html" not in fragment.text
    assert "BERT is designed" in fragment.text
    full = await client.get(f"/runs/{run_id}/claims/{cv}")
    assert "<html" in full.text and "BERT is designed" in full.text


@pytest.mark.db
async def test_claims_filters_and_unverifiable_reason(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/claims?status=UNVERIFIABLE")).text
    assert "Tài liệu được trích chưa tìm thấy trong các nguồn đã tra." in html
    flagged = (await client.get(f"/runs/{run_id}/claims?flag=1")).text
    assert flagged.count('class="claim-item"') == 3


@pytest.mark.db
@pytest.mark.parametrize("query", ["status=NOPE", "ref=abc", "c=not-a-uuid"])
async def test_invalid_query_400(
    client: httpx.AsyncClient, session_factory: SessionFactory, query: str
) -> None:
    run_id = await sample_run(client, session_factory)
    assert (await client.get(f"/runs/{run_id}/claims?{query}")).status_code == 400


@pytest.mark.db
async def test_reference_markup_escaped(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        await s.execute(
            update(ReferenceEntry)
            .where(ReferenceEntry.order_index == 0)
            .values(raw_text="<img src=x onerror=alert(1)>")
        )
        await s.commit()
    html = (await client.get(f"/runs/{run_id}/references")).text
    assert "&lt;img src=x onerror=alert(1)&gt;" in html and "<img src=x" not in html


@pytest.mark.db
async def test_markdown_download_headers(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    response = await client.get(f"/runs/{run_id}/report.md")
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="citeproof-{str(run_id)[:8]}.md"'
    )
    assert "## Gợi ý hành động" in response.text


@pytest.mark.db
async def test_foreign_report_404(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory, email="a@b.vn")
    client.cookies.clear()
    from tests.web.conftest import login

    await login(client, session_factory, email="b@b.vn")
    for path in ("report", "references", "claims", "missing", "report.md"):
        assert (await client.get(f"/runs/{run_id}/{path}")).status_code == 404, path


@pytest.mark.db
async def test_missing_tab_upload_disabled(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/missing")).text
    assert "Speech and Language Processing" in html
    assert "disabled" in html and "Tải nguồn lên sẽ có ở bản sau." in html


@pytest.mark.db
async def test_admin_sees_model_facts(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory, email="ad@b.vn", role=UserRole.ADMIN)
    html = (await client.get(f"/runs/{run_id}/claims")).text
    assert "Prompt" in html and "sample" in html


@pytest.mark.db
async def test_report_head_actions(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/report")).text
    assert f'href="/runs/{run_id}/report.md"' in html
    assert 'data-dialog="delete-dialog"' in html and "Xóa vĩnh viễn" in html
    assert 'value="rerun"' not in html  # bài mẫu


@pytest.mark.db
async def test_unresolved_claim_page(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    async with session_factory() as s:
        cv_id = await add_claim_verification(s, run_id, entry_id=None, sentence=UNRESOLVED_SENTENCE)
        await s.commit()
    html = (await client.get(f"/runs/{run_id}/claims?c={cv_id}")).text
    assert UNRESOLVED_SENTENCE in html
    assert f"Nguồn {UNRESOLVED_REF} nói" in html
    assert REASONS[UnverifiableReason.REFERENCE_UNRESOLVED] in html
    assert "Xem tài liệu [None]" not in html and "[None]" not in html
    fragment = await client.get(f"/runs/{run_id}/claims/{cv_id}", headers={"HX-Request": "true"})
    assert fragment.status_code == 200 and UNRESOLVED_REF in fragment.text
    _no_forbidden(html)


@pytest.mark.db
async def test_uncited_reference_page(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/references")).text
    assert html.count(UNCITED_REFERENCE) == 2  # [6] và [7] của bài mẫu
    cited = (await client.get(f"/runs/{run_id}/references?status=MATCHED")).text
    assert UNCITED_REFERENCE not in cited


@pytest.mark.db
async def test_missing_tab_lists_not_found_with_reason(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    run_id = await sample_run(client, session_factory)
    html = (await client.get(f"/runs/{run_id}/missing")).text
    assert "Phát hiện tin giả trên mạng xã hội tiếng Việt" in html  # [4], REFERENCE_NOT_FOUND
    assert REASONS[UnverifiableReason.REFERENCE_NOT_FOUND] in html
    assert REASONS[UnverifiableReason.NO_ACCESSIBLE_TEXT] in html
    _no_forbidden(html)
