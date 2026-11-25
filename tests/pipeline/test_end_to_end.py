"""Cả pipeline S1-S9 trên `vi_text_input.json` với registry thật (spec §7, 13 §A.4; T-601, T-602).

Không có mạng: một `httpx.MockTransport` giả GROBID (TEI `processCitationList` và toàn văn đã
ghi), Crossref (`crossref_doi_he.json`), OpenAlex (He et al. kèm `best_oa_location`), Semantic
Scholar (không có kết quả) và máy chủ bản mở (`en_ieee.pdf`, tải qua `SafeHttpClient` với
resolver giả). Kết quả: [1] chưa tìm thấy, [2] khớp và có toàn văn, [3] là luận văn (loại chưa
hỗ trợ tra cứu). LLM là `RoutedProvider` trả lời theo tác vụ: W2 giữ span rule, W3 dịch claim
sang tiếng Anh, W5 trích nguyên văn câu đầu của chunk đầu tiên.
"""

import html
import json
import logging
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevel,
    EvidenceRelation,
    ReferenceStatus,
    RunStatus,
    UnverifiableReason,
)
from citeproof.core.settings import Settings
from citeproof.db.models import (
    ClaimVerification,
    Document,
    Evidence,
    ReferenceCheck,
    SourceChunk,
    SourceFile,
    VerificationRun,
)
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.types import ProviderRequest, ProviderResponse
from citeproof.pipeline.registry import build_registry
from citeproof.services import documents
from citeproof.storage import LocalStorage
from citeproof.web.labels import CLAIM_STATUS, FORBIDDEN_WORDS
from tests.integrations.oa.fakes import pdf_stream
from tests.pipeline.helpers import (
    RoutedProvider,
    drain,
    json_response,
    make_test_deps,
    pipeline_settings,
)
from tests.web.conftest import PASSWORD, client_for, csrf_token_in, make_account

SessionFactory = async_sessionmaker[AsyncSession]

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = ROOT / "golden"
METADATA = ROOT / "metadata"
INPUT = json.loads((GOLDEN / "vi_text_input.json").read_text(encoding="utf-8"))
CITATIONS_TEI = (GOLDEN / "vi_text_input.citations.tei.xml").read_text(encoding="utf-8")
FULLTEXT_TEI = (GOLDEN / "en_ieee.fulltext.tei.xml").read_text(encoding="utf-8")
OA_PDF = (GOLDEN / "en_ieee.pdf").read_bytes()

GROBID_HOST = "grobid.test"
HE_DOI = "10.1109/cvpr.2016.90"
HE_OA_PDF = "https://papers.example.org/he-2016.pdf"
TRANSLATION = "Pre-trained encoders and residual connections stabilise the training of deep models"
EMAIL = "e2e@b.vn"

# Chuỗi riêng của bài, danh mục và nguồn: không bao giờ được xuất hiện trong log (13 §A.4).
BODY_SENTENCE = "Mạng residual cho phép huấn luyện mạng rất sâu"
REFERENCE_TITLE = "Xây dựng hệ thống hỏi đáp tiếng Việt"
SOURCE_SENTENCE = "Residual connections stabilise the training of deep models"


def _metadata(name: str) -> Any:
    return json.loads((METADATA / f"{name}.json").read_text(encoding="utf-8"))


def _openalex_he() -> dict[str, Any]:
    """Bản ghi OpenAlex của He et al. kèm `best_oa_location` trỏ tới bản PDF mở giả."""
    return {
        "id": "https://openalex.org/W2194775991",
        "doi": f"https://doi.org/{HE_DOI}",
        "title": "Deep Residual Learning for Image Recognition",
        "display_name": "Deep Residual Learning for Image Recognition",
        "publication_year": 2016,
        "type": "proceedings-article",
        "authorships": [
            {"author_position": "first", "author": {"display_name": "Kaiming He"}},
            {"author_position": "middle", "author": {"display_name": "Xiangyu Zhang"}},
        ],
        "best_oa_location": {"pdf_url": HE_OA_PDF, "license": "cc-by"},
    }


@dataclass
class FakeServices:
    """GROBID, ba API metadata và máy chủ bản mở giả; ghi lại host và đường dẫn mọi request."""

    visited: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", request.url.host).split(":")[0]
        path = unquote(request.url.path)
        self.visited.append(f"{host}{path}")
        if host == GROBID_HOST:
            if path == "/api/processCitationList":
                return httpx.Response(200, text=CITATIONS_TEI)
            if path == "/api/processFulltextDocument":
                return httpx.Response(200, text=FULLTEXT_TEI)
            return httpx.Response(404)
        if host == "api.crossref.org":
            if path.lower() == f"/works/{HE_DOI}":
                return httpx.Response(200, json=_metadata("crossref_doi_he"))
            if path == "/works":
                return httpx.Response(200, json={"status": "ok", "message": {"items": []}})
            return httpx.Response(404)
        if host == "api.openalex.org":
            if path.lower() == f"/works/doi:{HE_DOI}":
                return httpx.Response(200, json=_openalex_he())
            if path == "/works":
                return httpx.Response(200, json={"results": []})
            return httpx.Response(404)
        if host == "api.semanticscholar.org":
            if path.startswith("/graph/v1/paper/search"):
                return httpx.Response(200, json={"total": 0, "data": []})
            return httpx.Response(404)
        if f"https://{host}{path}" == HE_OA_PDF:
            return pdf_stream(OA_PDF)
        return httpx.Response(404, stream=httpx.ByteStream(b""))


# --- LLM giả --------------------------------------------------------------------------------

_CHUNK = re.compile(r'<chunk id="(chunk_\d+)"[^>]*>\n(.*?)\n</chunk>', re.DOTALL)


def _w2(request: ProviderRequest) -> ProviderResponse:
    return json_response({"items": []})  # giữ span của rule


def _w3(request: ProviderRequest) -> ProviderResponse:
    return json_response({"text_en": TRANSLATION, "kept_terms": []})


def _w5(request: ProviderRequest) -> ProviderResponse:
    """Câu đầu của chunk đầu tiên làm bằng chứng SUPPORTS (nguyên văn)."""
    chunks = _CHUNK.findall(request.messages[-1]["content"])
    if not chunks:
        return json_response(
            {
                "status": "UNVERIFIABLE",
                "evidence": [],
                "abstain_cause": "CONTEXT_INCONCLUSIVE",
                "explanation": "The chunks do not settle the claim.",
            }
        )
    chunk_id, text = chunks[0]
    text = html.unescape(text)
    end = text.find(". ")
    quote = text[: end + 1] if end > 0 else text
    return json_response(
        {
            "status": "SUPPORTED",
            "evidence": [{"chunk_id": chunk_id, "quote": quote, "relation": "SUPPORTS"}],
            "abstain_cause": None,
            "explanation": "Nguồn nêu đúng điều này.",
            "limitations": None,
        },
        model="claude-sonnet-5-5",
    )


def routed_llm() -> RoutedProvider:
    return RoutedProvider({"W2": _w2, "W3": _w3, "W5": _w5})


# --- Chạy pipeline --------------------------------------------------------------------------


def _settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"grobid_url": f"http://{GROBID_HOST}"})


@dataclass(frozen=True)
class Done:
    run_id: uuid.UUID
    document_id: uuid.UUID
    services: FakeServices


async def _run_pipeline(
    session_factory: SessionFactory, settings: Settings, provider: LLMProvider | None
) -> Done:
    user = await make_account(session_factory, email=EMAIL)
    async with session_factory() as session:
        document, run_id = await documents.create_text_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            title="Bài thử toàn pipeline",
            body=INPUT["body"],
            references="\n".join(INPUT["references"]),
        )
        await session.commit()
    services = FakeServices()
    deps = make_test_deps(
        settings, session_factory, provider=provider, transport=httpx.MockTransport(services)
    )
    registry = build_registry(deps)
    await drain(session_factory, registry, pipeline_settings())
    return Done(run_id=run_id, document_id=document.id, services=services)


async def _rows(session_factory: SessionFactory, run_id: uuid.UUID) -> list[ClaimVerification]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(ClaimVerification).where(ClaimVerification.run_id == run_id)
                )
            ).scalars()
        )


async def _ref_statuses(session_factory: SessionFactory, run_id: uuid.UUID) -> list[str]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(ReferenceCheck.status).where(ReferenceCheck.run_id == run_id)
                )
            ).scalars()
        )


@pytest.mark.db
async def test_pipeline_end_to_end_text_document(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = _settings(settings)
    provider = routed_llm()
    done = await _run_pipeline(session_factory, settings, provider)

    async with session_factory() as session:
        run = await session.get(VerificationRun, done.run_id)
        document = await session.get(Document, done.document_id)
        assert run is not None and document is not None
        assert run.status == RunStatus.COMPLETED.value, (run.status, run.warnings, run.error)
        assert run.warnings == []
        assert document.language == "vi"
        full_text = await session.scalar(
            select(SourceFile.id).where(SourceFile.content_level == ContentLevel.FULL_TEXT.value)
        )
        assert full_text is not None
        evidence = (
            await session.execute(
                select(Evidence, SourceChunk)
                .join(SourceChunk, SourceChunk.id == Evidence.source_chunk_id)
                .join(ClaimVerification, ClaimVerification.id == Evidence.claim_verification_id)
                .where(ClaimVerification.run_id == done.run_id)
            )
        ).all()
    statuses = await _ref_statuses(session_factory, done.run_id)
    assert ReferenceStatus.MATCHED.value in statuses
    rows = await _rows(session_factory, done.run_id)
    supported = [r for r in rows if r.status == ClaimStatus.SUPPORTED.value]
    assert supported, [(r.status, r.unverifiable_reason) for r in rows]
    assert evidence and all(e.quote_text in chunk.text for e, chunk in evidence)
    assert any(e.relation == EvidenceRelation.SUPPORTS.value for e, _ in evidence)
    assert all(r.verifier == "V2" for r in rows)
    assert {"W2", "W3", "W5"} <= {task for task, _ in provider.requests}
    # S9: chi phí lần chạy = tổng `llm_calls.cost_estimate`.
    assert run.cost_estimate > Decimal(0)
    # Devlin [1] không có trong các nguồn giả → nhận định chờ toàn văn (QĐ-31).
    assert any(r.unverifiable_reason == UnverifiableReason.REFERENCE_NOT_FOUND for r in rows)

    async with client_for(settings) as client:
        page = await client.get("/login")
        response = await client.post(
            "/login",
            data={"email": EMAIL, "password": PASSWORD, "csrf_token": csrf_token_in(page.text)},
        )
        assert response.status_code == 303
        claims = await client.get(f"/runs/{done.run_id}/claims?status=SUPPORTED")
        assert claims.status_code == 200
        assert CLAIM_STATUS[ClaimStatus.SUPPORTED].label in claims.text
        assert 'class="quote SUPPORTS"' in claims.text
        markdown = (await client.get(f"/runs/{done.run_id}/report.md")).text
        for page_text in (claims.text, markdown):
            for word in FORBIDDEN_WORDS:
                assert word not in page_text.lower(), word


@pytest.mark.db
async def test_pipeline_end_to_end_without_llm(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = _settings(settings)
    done = await _run_pipeline(session_factory, settings, None)
    async with session_factory() as session:
        run = await session.get(VerificationRun, done.run_id)
    assert run is not None
    assert run.status == RunStatus.COMPLETED_WITH_WARNINGS.value, (run.status, run.error)
    assert run.warnings == ["LLM_NOT_CONFIGURED"]
    assert run.cost_estimate == Decimal(0)
    statuses = await _ref_statuses(session_factory, done.run_id)
    assert len(statuses) == 3 and ReferenceStatus.MATCHED.value in statuses
    rows = await _rows(session_factory, done.run_id)
    assert rows and all(r.status == ClaimStatus.UNVERIFIABLE.value for r in rows)
    assert all(r.unverifiable_reason for r in rows)


def _log_values(record: logging.LogRecord) -> Iterator[str]:
    yield record.getMessage()
    standard = logging.makeLogRecord({}).__dict__
    for key, value in record.__dict__.items():
        if key not in standard:
            yield repr(value)
    if record.exc_text:
        yield record.exc_text


@pytest.mark.db
async def test_pipeline_logs_contain_no_document_text(
    session_factory: SessionFactory,
    clean_db: None,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = _settings(settings)
    done = await _run_pipeline(session_factory, settings, routed_llm())
    async with session_factory() as session:
        chunk = await session.scalar(
            select(SourceChunk.text).where(SourceChunk.text.contains(SOURCE_SENTENCE)).limit(1)
        )
    assert chunk is not None  # nguồn thật sự đã được lập chỉ mục
    assert done.services.visited  # pipeline thật sự đã gọi các dịch vụ giả
    secrets = (BODY_SENTENCE, REFERENCE_TITLE, SOURCE_SENTENCE, chunk[:60], HE_OA_PDF)
    assert caplog.records
    for record in caplog.records:
        for value in _log_values(record):
            for secret in secrets:
                assert secret not in value, (record.name, record.levelname, secret)
