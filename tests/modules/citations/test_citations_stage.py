"""S4 — trích dẫn, liên kết, claim (spec §7.4; T-401, T-402; QĐ-13, QĐ-16; Review Focus #3).

Pipeline chạy thật S1, S2 và S4 qua `drain` (các stage khác là `CountingStage`); GROBID được
giả bằng `httpx.MockTransport` phát lại TEI đã ghi của bộ golden.
"""

import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import LinkMethod, RunStatus, StageName
from citeproof.core.errors import LeaseLostError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    Document,
    LLMCall,
    ReferenceEntry,
    VerificationRun,
)
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import ProviderResponse
from citeproof.modules.citations.stage import CitationsStage
from citeproof.modules.documents.stage import ParseStage
from citeproof.modules.references.stage_extract import ExtractReferencesStage
from citeproof.parsing.model import ParsedDocument
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.stages import PipelineStage, StageContext, StageRegistry, StageResult
from citeproof.services import documents
from citeproof.storage import LocalStorage
from tests.golden.cases import GoldenCase
from tests.pipeline.helpers import (
    CountingStage,
    drain,
    make_test_deps,
    pipeline_settings,
    stage_context,
)
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]
_SPACE = re.compile(r"\s+")


def _grobid(case: GoldenCase | None = None) -> httpx.MockTransport:
    """GROBID giả: TEI toàn văn và `processCitationList` đã ghi; thiếu TEI → 500."""
    fulltext = case.fulltext_tei if case is not None else None
    citations = case.citations_tei if case is not None else None

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        recorded = fulltext if path == "/api/processFulltextDocument" else citations
        if recorded is None:
            return httpx.Response(500, text="[ERR]")
        return httpx.Response(200, text=recorded.read_text(encoding="utf-8"))

    return httpx.MockTransport(handler)


def _registry(deps: PipelineDeps, s4: PipelineStage | None = None) -> StageRegistry:
    real: dict[StageName, PipelineStage] = {
        StageName.S1: ParseStage(deps),
        StageName.S2: ExtractReferencesStage(deps),
        StageName.S4: s4 or CitationsStage(deps),
    }
    return StageRegistry(real.get(name) or CountingStage(name) for name in StageName)


async def _text_document(
    session_factory: SessionFactory, settings: Settings, *, body: str, references: str
) -> tuple[Document, uuid.UUID]:
    async with session_factory() as session:
        user = await new_user(session, email=f"{uuid.uuid4().hex[:12]}@b.vn")
        document, run_id = await documents.create_text_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            title="Bài thử",
            body=body,
            references=references,
        )
        await session.commit()
    return document, run_id


async def _golden_document(
    session_factory: SessionFactory, settings: Settings, case: GoldenCase
) -> tuple[Document, uuid.UUID]:
    if case.input_path.suffix == ".json":
        payload = json.loads(case.input_path.read_text(encoding="utf-8"))
        return await _text_document(
            session_factory,
            settings,
            body=payload["body"],
            references="\n".join(payload["references"]),
        )
    async with session_factory() as session:
        user = await new_user(session, email=f"{uuid.uuid4().hex[:12]}@b.vn")
        document, run_id = await documents.create_file_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            filename=f"bai{case.input_path.suffix}",
            data=case.input_path.read_bytes(),
        )
        await session.commit()
    return document, run_id


@dataclass(frozen=True)
class _Row:
    citation: Citation
    links: list[CitationLink]
    claim: Claim


async def _rows(session_factory: SessionFactory, document_id: uuid.UUID) -> list[_Row]:
    async with session_factory() as session:
        citations = list(
            (
                await session.execute(
                    select(Citation)
                    .where(Citation.document_id == document_id)
                    .order_by(Citation.char_start)
                )
            ).scalars()
        )
        links: dict[uuid.UUID, list[CitationLink]] = defaultdict(list)
        for link in (
            await session.execute(
                select(CitationLink).where(CitationLink.citation_id.in_([c.id for c in citations]))
            )
        ).scalars():
            links[link.citation_id].append(link)
        claims = {
            claim.citation_id: claim
            for claim in (
                await session.execute(select(Claim).where(Claim.document_id == document_id))
            ).scalars()
        }
    assert len(claims) == len(citations)  # một claim cho mỗi marker
    return [_Row(c, links[c.id], claims[c.id]) for c in citations]


async def _ref_names(
    session_factory: SessionFactory, document_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    """Tên golden của mỗi mục: nhãn in trong bài, hoặc vị trí 1-based khi không có nhãn."""
    async with session_factory() as session:
        entries = (
            await session.execute(
                select(ReferenceEntry).where(ReferenceEntry.document_id == document_id)
            )
        ).scalars()
        return {e.id: e.parsed.get("label") or str(e.order_index + 1) for e in entries}


async def _parsed(
    session_factory: SessionFactory, settings: Settings, document_id: uuid.UUID
) -> ParsedDocument:
    async with session_factory() as session:
        key = await session.scalar(select(Document.parsed_key).where(Document.id == document_id))
    assert key is not None
    data = await LocalStorage(settings.storage_dir).get(key)
    return ParsedDocument.model_validate_json(data)


async def _run_status(session_factory: SessionFactory, run_id: uuid.UUID) -> str | None:
    async with session_factory() as session:
        return await session.scalar(
            select(VerificationRun.status).where(VerificationRun.id == run_id)
        )


def _linked(row: _Row, names: dict[uuid.UUID, str]) -> list[str | None]:
    """Tên golden của các mục được link (NULL → None), sắp theo chuỗi: thứ tự link không lưu."""
    linked = [
        None if link.reference_entry_id is None else names[link.reference_entry_id]
        for link in row.links
    ]
    return sorted(linked, key=str)


def _marker_key(text: str) -> str:
    """Marker so sánh với golden: bỏ khoảng trắng, gạch ngang dài thành gạch nối (TEI của
    GROBID ghi "[2]- [4]" cho khoảng in bằng gạch ngang dài trong PDF).
    """
    return _SPACE.sub("", text).replace("\u2013", "-")


# --- Bộ golden ------------------------------------------------------------------------------


@pytest.mark.db
async def test_s4_golden_links(
    golden_case: GoldenCase, session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    expected = golden_case.expected
    assert expected is not None
    document, run_id = await _golden_document(session_factory, settings, golden_case)
    deps = make_test_deps(settings, session_factory, transport=_grobid(golden_case))
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _run_status(session_factory, run_id) in (
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_WARNINGS.value,
    )

    rows = await _rows(session_factory, document.id)
    names = await _ref_names(session_factory, document.id)
    text = (await _parsed(session_factory, settings, document.id)).text()
    assert len(rows) == len(expected.citations), golden_case.name
    for row, cite in zip(rows, expected.citations, strict=True):
        where = f"{golden_case.name}: {cite.marker}"
        citation, claim = row.citation, row.claim
        assert _marker_key(citation.marker_text) == _marker_key(cite.marker), where
        assert text[citation.char_start : citation.char_end] == citation.marker_text, where
        assert citation.marker_text in citation.sentence_text, where
        assert _linked(row, names) == sorted(cite.links, key=str), where
        kinds = {link.link_method for link in row.links}
        numeric = citation.marker_text.startswith("[")
        assert kinds == {LinkMethod.NUMERIC.value if numeric else LinkMethod.AUTHOR_YEAR.value}
        assert _SPACE.sub(" ", claim.claim_text) == cite.claim, where
        assert claim.claim_text in citation.sentence_text, where
        assert claim.extraction_method == "rule" and claim.is_claim, where
        assert claim.language in ("vi", "en"), where


# --- Liên kết, bỏ qua, ghi một lần ----------------------------------------------------------

REFERENCES = "\n".join(
    [
        "[1] Nguyễn Văn An (2020), Phân loại văn bản tiếng Việt, Tạp chí Mẫu.",
        "[2] Trần Thị Bích (2021), Nhận dạng thực thể có tên, Luận văn thạc sĩ.",
    ]
)


@pytest.mark.db
async def test_unresolved_marker_creates_null_link(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    body = (
        "Dữ liệu năm [2019] gồm [n = 30] mẫu tại điểm [x, y] trong mặt phẳng. "
        "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1, 1, 2]. "
        "Một số tác giả khác cũng có kết luận tương tự [57]. "
        "Kết quả này được nhắc lại (Smith, 2010; Nguyễn Văn An, 2020)."
    )
    document, run_id = await _text_document(
        session_factory, settings, body=body, references=REFERENCES
    )
    deps = make_test_deps(settings, session_factory)
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED_WITH_WARNINGS.value

    rows = await _rows(session_factory, document.id)
    names = await _ref_names(session_factory, document.id)
    assert [row.citation.marker_text for row in rows] == [
        "[1, 1, 2]",
        "[57]",
        "(Smith, 2010; Nguyễn Văn An, 2020)",
    ]
    linked = [_linked(row, names) for row in rows]
    assert linked[0] == ["1", "2"]  # "[1, 1, 2]": mỗi tài liệu một link (F41)
    assert linked[1] == [None]
    assert linked[2] == ["1", None]
    assert [link.link_method for link in rows[1].links] == [LinkMethod.NUMERIC.value]
    assert {link.link_method for link in rows[2].links} == {LinkMethod.AUTHOR_YEAR.value}
    unresolved = rows[1]
    assert unresolved.claim.claim_text == "Một số tác giả khác cũng có kết luận tương tự"
    assert unresolved.claim.context_text.startswith("Phân loại văn bản tiếng Việt")
    assert unresolved.citation.sentence_text == (
        "Một số tác giả khác cũng có kết luận tương tự [57]."
    )
    assert unresolved.citation.page is None and unresolved.citation.section is None


@pytest.mark.db
async def test_page_locator_markers_link_reference_numbers(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    body = (
        "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1, tr. 25; 2, tr. 7]. "
        "Giáo trình định nghĩa khái niệm này rất rõ ràng [2: 12-15]. "
        "Một kết quả khác được nêu ở nguồn ngoài danh mục [57, trang 3]. "
        "Xem thêm [tr. 25] trong cùng giáo trình. "
        "Hai nguồn được trích cùng lúc trong một ngoặc [1, tr. 25, 2, tr. 7]."
    )
    document, run_id = await _text_document(
        session_factory, settings, body=body, references=REFERENCES
    )
    deps = make_test_deps(settings, session_factory)
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED_WITH_WARNINGS.value

    rows = await _rows(session_factory, document.id)
    names = await _ref_names(session_factory, document.id)
    assert [row.citation.marker_text for row in rows] == [
        "[1, tr. 25; 2, tr. 7]",
        "[2: 12-15]",
        "[57, trang 3]",
        "[1, tr. 25, 2, tr. 7]",
    ]
    assert [_linked(row, names) for row in rows] == [["1", "2"], ["2"], [None], ["1", "2"]]
    assert {link.link_method for row in rows for link in row.links} == {LinkMethod.NUMERIC.value}
    assert rows[0].claim.claim_text == "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi"
    text = (await _parsed(session_factory, settings, document.id)).text()
    for row in rows:
        assert text[row.citation.char_start : row.citation.char_end] == row.citation.marker_text


@pytest.mark.db
async def test_s4_without_reference_entries_writes_nothing(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Danh mục chỉ có nhãn: S2 không tạo mục nào (`NO_REFERENCE_SECTION`), S4 không có việc.
    document, run_id = await _text_document(
        session_factory, settings, body="Mô hình đạt kết quả tốt [1].", references="[1]"
    )
    deps = make_test_deps(settings, session_factory)
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value
    assert await _rows(session_factory, document.id) == []


@pytest.mark.db
async def test_s4_reuses_citations_of_document(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    body = "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1]."
    document, _ = await _text_document(session_factory, settings, body=body, references=REFERENCES)
    deps = make_test_deps(settings, session_factory)
    await drain(session_factory, _registry(deps), pipeline_settings())
    first = await _rows(session_factory, document.id)
    assert len(first) == 1

    async with session_factory() as session:
        run_id = await create_run(
            session, document_id=document.id, user_id=document.user_id, config={}
        )
        await session.commit()
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value
    second = await _rows(session_factory, document.id)
    assert [row.citation.id for row in second] == [row.citation.id for row in first]
    assert [row.claim.id for row in second] == [row.claim.id for row in first]


@pytest.mark.db
async def test_s4_lease_lost_writes_nothing(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    body = "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1]."
    _, run_id = await _text_document(session_factory, settings, body=body, references=REFERENCES)
    deps = make_test_deps(settings, session_factory)
    await drain(session_factory, _registry(deps, CountingStage(StageName.S4)), pipeline_settings())
    with pytest.raises(LeaseLostError):
        await CitationsStage(deps).run(stage_context(run_id, stage=StageName.S4))
    async with session_factory() as session:
        for model in (Citation, CitationLink, Claim):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


# --- W2 (claim.extract) -----------------------------------------------------------------------

W2_BODY = "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1]. Xem thêm [2]."
W2_SENTENCE = "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi [1]."


class _Recording:
    """Bọc S4 thật để lấy `StageResult` của lần chạy qua `drain`."""

    name = StageName.S4

    def __init__(self, deps: PipelineDeps) -> None:
        self._inner = CitationsStage(deps)
        self.results: list[StageResult] = []

    async def run(self, ctx: StageContext) -> StageResult:
        result = await self._inner.run(ctx)
        self.results.append(result)
        return result


def _w2_response(*items: dict[str, object]) -> ProviderResponse:
    return ProviderResponse(json.dumps({"items": list(items)}), 100, 50, "end_turn")


def _w2_item(
    marker_id: str, start: int, end: int, *, text: str, is_claim: bool = True
) -> dict[str, object]:
    return {
        "marker_id": marker_id,
        "is_claim": is_claim,
        "span_start": start,
        "span_end": end,
        "claim_text": text,
    }


@pytest.mark.db
async def test_s4_without_llm_warns(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document, _ = await _text_document(
        session_factory, settings, body=W2_BODY, references=REFERENCES
    )
    deps = make_test_deps(settings, session_factory)
    recording = _Recording(deps)
    await drain(session_factory, _registry(deps, recording), pipeline_settings())
    assert [r.warnings for r in recording.results] == [("LLM_NOT_CONFIGURED",)]
    rows = await _rows(session_factory, document.id)
    assert {row.claim.extraction_method for row in rows} == {"rule"}


@pytest.mark.db
async def test_s4_with_scripted_w2(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document, _ = await _text_document(
        session_factory, settings, body=W2_BODY, references=REFERENCES
    )
    refined = "văn bản tiếng Việt đã được nghiên cứu rộng rãi"
    start = W2_SENTENCE.index(refined)
    second = "Xem thêm [2]."
    provider = ScriptedProvider(
        [
            _w2_response(
                _w2_item("m1", start, start + len(refined), text=refined),
                _w2_item("m2", 0, len(second), text=second, is_claim=False),
            )
        ]
    )
    deps = make_test_deps(settings, session_factory, provider=provider)
    recording = _Recording(deps)
    await drain(session_factory, _registry(deps, recording), pipeline_settings())
    assert [r.warnings for r in recording.results] == [()]
    assert len(provider.requests) == 1  # một đoạn có marker: một lời gọi
    rows = await _rows(session_factory, document.id)
    assert [row.claim.claim_text for row in rows] == [refined, second]
    assert [row.claim.is_claim for row in rows] == [True, False]
    assert rows[0].claim.extraction_method == "llm:claim.extract/v1"
    async with session_factory() as session:
        calls = (await session.execute(select(LLMCall.task, LLMCall.stage))).all()
    assert [(c.task, c.stage) for c in calls] == [("claim.extract", "S4")]


@pytest.mark.db
async def test_w2_output_invalid_twice_keeps_rules(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document, run_id = await _text_document(
        session_factory, settings, body=W2_BODY, references=REFERENCES
    )
    bad = ProviderResponse('{"items": "nope"}', 10, 5, "end_turn")
    deps = make_test_deps(settings, session_factory, provider=ScriptedProvider([bad, bad]))
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value
    rows = await _rows(session_factory, document.id)
    assert len(rows) == 2
    assert {row.claim.extraction_method for row in rows} == {"rule"}
    assert rows[0].claim.claim_text == "Phân loại văn bản tiếng Việt đã được nghiên cứu rộng rãi"
