"""S2: tách và parse danh mục thành `reference_entries` (spec §7.2, QĐ-04, QĐ-13).

GROBID được giả bằng `httpx.MockTransport` phát lại TEI đã ghi; W1 qua `ScriptedProvider`.
"""

import json
import unicodedata
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl
from xml.etree.ElementTree import fromstring

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ParseMethod, RefType, StageName
from citeproof.core.errors import LeaseLostError, RetryableError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, LLMCall, ReferenceEntry, User
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import ProviderResponse
from citeproof.modules.documents.stage import ParseStage
from citeproof.modules.references.normalize import family_name, normalize_doi, title_norm
from citeproof.modules.references.rules import parse_reference_rules
from citeproof.modules.references.stage_extract import ExtractReferencesStage, merge_fields
from citeproof.parsing.model import ParsedDocument, RawReference
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import claim_next
from citeproof.pipeline.runs import complete_stage, create_run
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.services import documents
from citeproof.storage import LocalStorage
from tests.golden.cases import GOLDEN_DIR, GoldenCase
from tests.pipeline.helpers import make_test_deps, stage_context
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]
TEI_NS = "{http://www.tei-c.org/ns/1.0}"
BODY = "Mô hình đạt độ chính xác cao hơn [1]. Kết quả tương tự [2]."
UNPARSEABLE = "NGUYỄN VĂN AN. PHÂN LOẠI VĂN BẢN TIẾNG VIỆT. TẠP CHÍ MẪU 2020"
OTHER_UNPARSEABLE = "TRẦN THỊ BÍCH. NHẬN DẠNG THỰC THỂ CÓ TÊN. LUẬN VĂN 2021"
PARSEABLE = "Lê Hoàng Cường (2019), Giáo trình xử lý ngôn ngữ tự nhiên, NXB Giáo dục."


class GrobidStub:
    """GROBID giả: TEI toàn văn và `processCitationList` đã ghi, hoặc mã lỗi/ngắt kết nối."""

    def __init__(
        self,
        *,
        fulltext: str | None = None,
        citations: str | None = None,
        status: int = 200,
        down: bool = False,
    ) -> None:
        self.fulltext = fulltext
        self.citations = citations
        self.status = status
        self.down = down
        self.citation_requests: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/processFulltextDocument":
            assert self.fulltext is not None
            return httpx.Response(200, text=self.fulltext)
        assert request.url.path == "/api/processCitationList"
        form = parse_qsl(request.content.decode())
        self.citation_requests.append([value for key, value in form if key == "citations"])
        if self.down:
            raise httpx.ConnectError("connection refused")
        if self.citations is None or self.status != 200:
            return httpx.Response(self.status if self.status != 200 else 500, text="[ERR]")
        return httpx.Response(200, text=self.citations)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _raw_notes(tei: str) -> list[str]:
    root = fromstring(tei)
    return [
        note.text or ""
        for note in root.iter(f"{TEI_NS}note")
        if note.get("type") == "raw_reference"
    ]


async def _claim(session_factory: SessionFactory, stage: StageName) -> StageContext:
    async with session_factory() as session:
        lease = await claim_next(session, "w1", timedelta(seconds=120), max_attempts=3)
        await session.commit()
    assert lease is not None and lease.stage is stage
    return StageContext(run_id=lease.run_id, lease=lease, max_attempts=3)


async def _through_s1(session_factory: SessionFactory, deps: PipelineDeps) -> StageContext:
    """Chạy S1 thật cho job đang chờ, ghi DONE rồi nhận job S2."""
    ctx = await _claim(session_factory, StageName.S1)
    result = await ParseStage(deps).run(ctx)
    async with session_factory() as session:
        assert await complete_stage(session, ctx.lease, result)
        await session.commit()
    return await _claim(session_factory, StageName.S2)


async def _text_document(
    session_factory: SessionFactory, settings: Settings, references: str, body: str = BODY
) -> tuple[User, Document]:
    async with session_factory() as session:
        user = await new_user(session, email=f"{uuid.uuid4().hex[:12]}@b.vn")
        document, _ = await documents.create_text_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            title="Bài thử",
            body=body,
            references=references,
        )
        await session.commit()
    return user, document


async def _file_document(
    session_factory: SessionFactory, settings: Settings, data: bytes, filename: str
) -> tuple[User, Document]:
    async with session_factory() as session:
        user = await new_user(session, email=f"{uuid.uuid4().hex[:12]}@b.vn")
        document, _ = await documents.create_file_document(
            session,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            filename=filename,
            data=data,
        )
        await session.commit()
    return user, document


async def _golden_document(
    session_factory: SessionFactory, settings: Settings, case: GoldenCase
) -> Document:
    if case.input_path.suffix == ".json":
        payload = json.loads(case.input_path.read_text(encoding="utf-8"))
        _, document = await _text_document(
            session_factory, settings, "\n".join(payload["references"]), body=payload["body"]
        )
        return document
    data = case.input_path.read_bytes()
    _, document = await _file_document(
        session_factory, settings, data, f"bai{case.input_path.suffix}"
    )
    return document


async def _entries(session_factory: SessionFactory, document_id: uuid.UUID) -> list[ReferenceEntry]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(ReferenceEntry)
                    .where(ReferenceEntry.document_id == document_id)
                    .order_by(ReferenceEntry.order_index)
                )
            ).scalars()
        )


async def _parse_warnings(session_factory: SessionFactory, document_id: uuid.UUID) -> list[Any]:
    async with session_factory() as session:
        warnings: list[Any] = await session.scalar(
            select(Document.parse_warnings).where(Document.id == document_id)
        )
    return warnings


def _w1_response(*items: dict[str, Any]) -> ProviderResponse:
    full = [
        {
            "ref_id": "r1",
            "title": None,
            "authors": [],
            "year": None,
            "venue": None,
            "doi": None,
            "isbn": None,
            "url": None,
            "ref_type": "UNKNOWN",
            "unparseable": False,
        }
        | fields
        for fields in items
    ]
    return ProviderResponse(json.dumps({"items": full}), 120, 60, "end_turn")


# --- Bộ golden ------------------------------------------------------------------------------


@pytest.mark.db
async def test_golden_reference_fields(
    golden_case: GoldenCase, session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    expected = golden_case.expected
    assert expected is not None
    stub = GrobidStub(
        fulltext=(
            golden_case.fulltext_tei.read_text(encoding="utf-8")
            if golden_case.fulltext_tei
            else None
        ),
        citations=(
            golden_case.citations_tei.read_text(encoding="utf-8")
            if golden_case.citations_tei
            else None
        ),
    )
    document = await _golden_document(session_factory, settings, golden_case)
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    ctx = await _through_s1(session_factory, deps)
    assert await ExtractReferencesStage(deps).run(ctx) == StageResult()

    entries = await _entries(session_factory, document.id)
    assert len(entries) == len(expected.references)
    for index, (entry, ref) in enumerate(zip(entries, expected.references, strict=True)):
        where = f"{golden_case.name}[{index}]"
        assert entry.order_index == index, where
        assert entry.raw_text, where
        parsed = entry.parsed
        assert ref.title is not None
        assert title_norm(parsed["title"]) == title_norm(ref.title), where
        assert parsed["year"] == ref.year, where
        assert entry.doi_normalized == normalize_doi(ref.doi), where
        assert normalize_doi(parsed["doi"]) == normalize_doi(ref.doi), where
        assert parsed["authors"], where
        assert family_name(parsed["authors"][0]) == ref.first_author_family, where
        assert entry.ref_type == ref.ref_type.value, where
        # Nhãn in trong bài, hoặc vị trí 1-based khi bài không in nhãn (quy ước golden).
        assert parsed["label"] in (ref.label, None), where
        assert (parsed["label"] or str(index + 1)) == (ref.label or str(index + 1)), where
        assert entry.parse_method in (ParseMethod.GROBID.value, ParseMethod.RULE.value), where

    if not expected.references:
        codes = [w["code"] for w in await _parse_warnings(session_factory, document.id)]
        assert "NO_REFERENCE_SECTION" in codes
    if golden_case.citations_tei is not None and stub.citation_requests:
        # Chuỗi gửi GROBID (đã bỏ nhãn `[n]`) trùng chuỗi đã ghi fixture.
        assert stub.citation_requests == [
            _raw_notes(golden_case.citations_tei.read_text(encoding="utf-8"))
        ]
        assert all(not raw.startswith("[") for raw in stub.citation_requests[0])


@pytest.mark.db
async def test_multiline_entries_merged_and_raw_text_kept(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    data = (GOLDEN_DIR / "multiline_refs.docx").read_bytes()
    document = (await _file_document(session_factory, settings, data, "bai.docx"))[1]
    deps = make_test_deps(settings, session_factory)  # không GROBID: chỉ rule
    ctx = await _through_s1(session_factory, deps)
    assert await ExtractReferencesStage(deps).run(ctx) == StageResult()
    entries = await _entries(session_factory, document.id)
    assert len(entries) == 4
    assert [e.parsed["label"] for e in entries] == ["1", "2", "3", "4"]
    assert [e.order_index for e in entries] == [0, 1, 2, 3]
    assert entries[1].raw_text == (
        "Trần Thị Bích (2021), Nhận dạng thực thể có tên trong văn bản "
        "tiếng Việt, Luận văn thạc sĩ, Trường Đại học Mẫu, Hà Nội."
    )
    assert entries[1].parsed["title"] == "Nhận dạng thực thể có tên trong văn bản tiếng Việt"
    assert entries[1].ref_type == RefType.THESIS.value
    assert entries[2].raw_text.endswith("arXiv:1706.03762, doi:10.48550/arXiv.1706.03762.")
    assert entries[2].doi_normalized == "10.48550/arxiv.1706.03762"
    assert all(e.parse_method == ParseMethod.RULE.value for e in entries)


# --- W1 và cảnh báo LLM_NOT_CONFIGURED ------------------------------------------------------


@pytest.mark.db
async def test_w1_fields_not_in_raw_are_dropped(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    references = f"[1] {UNPARSEABLE}\n[2] {OTHER_UNPARSEABLE}\n[3] {PARSEABLE}"
    document = (await _text_document(session_factory, settings, references))[1]
    provider = ScriptedProvider(
        [
            _w1_response(
                {"ref_id": "r1", "title": "A Completely Different Title", "year": 2020,
                 "authors": ["Nguyễn Văn An"], "ref_type": "OTHER"},
                {"ref_id": "r2", "title": "Nhận dạng thực thể có tên", "year": 2021,
                 "ref_type": "THESIS"},
            )
        ]
    )  # fmt: skip
    deps = make_test_deps(settings, session_factory, provider=provider)
    ctx = await _through_s1(session_factory, deps)
    assert await ExtractReferencesStage(deps).run(ctx) == StageResult()

    # Chỉ hai mục chưa có tiêu đề và DOI được gửi W1, trong một lô.
    assert len(provider.requests) == 1
    prompt = provider.requests[0].messages[0]["content"]
    assert 'id="r2"' in prompt and 'id="r3"' not in prompt and "Giáo trình" not in prompt
    first, second, third = await _entries(session_factory, document.id)
    assert first.parsed["title"] is None
    assert first.parsed["year"] == 2020
    assert first.parsed["authors"] == ["Nguyễn Văn An"]
    assert first.ref_type == RefType.OTHER.value  # W1 ghi đè loại tài liệu (rule: ARTICLE)
    assert first.parse_method == ParseMethod.RULE.value  # tiêu đề W1 bị bỏ
    assert second.parsed["title"] == "Nhận dạng thực thể có tên"
    assert second.parse_method == ParseMethod.LLM.value
    assert second.ref_type == RefType.THESIS.value
    assert third.parse_method == ParseMethod.RULE.value
    assert third.ref_type == RefType.BOOK.value
    async with session_factory() as session:
        calls = (await session.execute(select(LLMCall.task, LLMCall.stage))).all()
    assert [(c.task, c.stage) for c in calls] == [("reference.parse", "S2")]


@pytest.mark.db
async def test_llm_warning_only_when_needed(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    _, parsed_doc = await _text_document(session_factory, settings, f"[1] {PARSEABLE}")
    deps = make_test_deps(settings, session_factory)
    assert deps.llm is None
    ctx = await _through_s1(session_factory, deps)
    assert await ExtractReferencesStage(deps).run(ctx) == StageResult()
    assert len(await _entries(session_factory, parsed_doc.id)) == 1

    references = f"[1] {PARSEABLE}\n[2] {UNPARSEABLE}"
    _, document = await _text_document(session_factory, settings, references)
    ctx = await _through_s1(session_factory, deps)
    result = await ExtractReferencesStage(deps).run(ctx)
    assert result.warnings == ("LLM_NOT_CONFIGURED",)
    first, second = await _entries(session_factory, document.id)
    assert first.parsed["title"] == "Giáo trình xử lý ngôn ngữ tự nhiên"
    assert second.parsed["title"] is None and second.parsed["year"] == 2020
    assert second.parse_method == ParseMethod.RULE.value


@pytest.mark.db
async def test_w1_retryable_error_degrades_only_on_last_attempt(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document = (await _text_document(session_factory, settings, f"[1] {UNPARSEABLE}"))[1]
    error = RetryableError("quá tải", code="LLM_RATE_LIMITED")
    provider = ScriptedProvider([error, error])
    deps = make_test_deps(settings, session_factory, provider=provider)
    ctx = await _through_s1(session_factory, deps)
    with pytest.raises(RetryableError):
        await ExtractReferencesStage(deps).run(ctx)
    assert await _entries(session_factory, document.id) == []
    last = StageContext(run_id=ctx.run_id, lease=ctx.lease, max_attempts=ctx.attempt)
    assert last.is_last_attempt
    assert await ExtractReferencesStage(deps).run(last) == StageResult()
    [entry] = await _entries(session_factory, document.id)
    assert entry.parsed["title"] is None and entry.parse_method == ParseMethod.RULE.value


# --- GROBID ---------------------------------------------------------------------------------


@pytest.mark.db
async def test_grobid_citations_used_for_unstructured_strings(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    tei = (GOLDEN_DIR / "vi_thesis_numeric.citations.tei.xml").read_text(encoding="utf-8")
    raws = _raw_notes(tei)
    references = "\n".join(f"[{n}] {raw}" for n, raw in enumerate(raws, start=1))
    document = (await _text_document(session_factory, settings, references))[1]
    stub = GrobidStub(citations=tei)
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    ctx = await _through_s1(session_factory, deps)
    assert await ExtractReferencesStage(deps).run(ctx) == StageResult()
    assert stub.citation_requests == [raws]  # nhãn `[n]` không gửi cho GROBID
    entries = await _entries(session_factory, document.id)
    assert [e.parse_method for e in entries] == [ParseMethod.GROBID.value] * 4
    assert entries[0].parsed["venue"] == "Tạp chí Khoa học và Công nghệ Mẫu"
    # Tác giả giữ cách viết trong bài, không phải tên GROBID tách sai ("Nguyễn Văn", "An").
    assert entries[0].parsed["authors"] == ["Nguyễn Văn An"]
    assert entries[3].parsed["title"] == "Giáo trình xử lý ngôn ngữ tự nhiên"
    assert [e.parsed["label"] for e in entries] == ["1", "2", "3", "4"]


@pytest.mark.db
@pytest.mark.parametrize("failure", ["down", "status_503", "status_500", "misaligned"])
async def test_grobid_failure_handling(
    failure: str, session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document = (await _text_document(session_factory, settings, f"[1] {PARSEABLE}"))[1]
    misaligned = (GOLDEN_DIR / "vi_thesis_numeric.citations.tei.xml").read_text(encoding="utf-8")
    stub = {
        "down": GrobidStub(down=True),
        "status_503": GrobidStub(citations="x", status=503),
        "status_500": GrobidStub(citations="x", status=500),
        "misaligned": GrobidStub(citations=misaligned),  # 4 biblStruct cho 1 chuỗi
    }[failure]
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    ctx = await _through_s1(session_factory, deps)
    stage = ExtractReferencesStage(deps)
    if failure in {"down", "status_503"}:
        # GROBID tạm thời không truy cập được: job thử lại, chỉ lần cuối mới dùng rule.
        with pytest.raises(RetryableError):
            await stage.run(ctx)
        assert await _entries(session_factory, document.id) == []
        ctx = StageContext(run_id=ctx.run_id, lease=ctx.lease, max_attempts=ctx.attempt)
    assert await stage.run(ctx) == StageResult()
    [entry] = await _entries(session_factory, document.id)
    assert entry.parse_method == ParseMethod.RULE.value
    assert entry.parsed["title"] == "Giáo trình xử lý ngôn ngữ tự nhiên"
    assert entry.parsed["venue"] is None


def test_merge_fields_precedence_and_nfc() -> None:
    nfd = unicodedata.normalize
    raw = "Nguyễn Văn An (2020), Phân loại văn bản tiếng Việt, Luận văn thạc sĩ, Hà Nội."
    grobid = ParsedReference(
        title=nfd("NFD", "Phân loại văn bản tiếng Việt, Luận văn thạc sĩ"),
        authors=[nfd("NFD", "Nguyễn Văn"), "An"],
        year=2020,
        venue=nfd("NFD", "Trường Đại học Mẫu"),
        doi="not-a-doi",
    )
    parsed, method = merge_fields(grobid, parse_reference_rules(raw), label="4")
    assert method is ParseMethod.GROBID
    assert parsed.title == "Phân loại văn bản tiếng Việt"  # NFC, bỏ phần xuất bản
    assert parsed.authors == ["Nguyễn Văn An"]  # cách viết trong bài, không phải tên GROBID
    assert parsed.venue == "Trường Đại học Mẫu"
    assert parsed.doi is None and parsed.label == "4"
    rule_only, rule_method = merge_fields(
        ParsedReference(authors=["J Devlin"]), parse_reference_rules("Ghi chú 2019."), label=None
    )
    assert rule_method is ParseMethod.RULE and rule_only.title is None
    assert rule_only.authors == ["J Devlin"] and rule_only.year == 2019


@pytest.mark.db
async def test_leading_label_stripped_before_grobid(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document = (await _text_document(session_factory, settings, f"[1] {PARSEABLE}"))[1]
    stub = GrobidStub()
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    ctx = await _through_s1(session_factory, deps)
    # Chuỗi thô còn nhãn `[n]` (ví dụ danh mục đã lưu từ nguồn khác): GROBID làm hỏng chuỗi
    # mở đầu bằng "[", nên nhãn bị bỏ khi gửi; chuỗi lưu vẫn giữ nguyên văn.
    raw = "[7] Smith, J. (2020). A short title. Journal X, 3(1), 1-9."
    async with session_factory() as session:
        key = await session.scalar(select(Document.parsed_key).where(Document.id == document.id))
    assert key is not None
    stored = ParsedDocument.model_validate_json(await deps.storage.get(key))
    await deps.storage.put(
        key, stored.with_references_raw([RawReference(raw=raw)]).model_dump_json().encode()
    )
    assert await ExtractReferencesStage(deps).run(ctx) == StageResult()
    assert stub.citation_requests == [["Smith, J. (2020). A short title. Journal X, 3(1), 1-9."]]
    [entry] = await _entries(session_factory, document.id)
    assert entry.raw_text == raw
    assert entry.parsed["title"] == "A short title"


# --- Tài liệu không có danh mục, chạy lại, lease ---------------------------------------------


@pytest.mark.db
async def test_no_reference_section_warning(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    data = (GOLDEN_DIR / "vi_tcvn3_legacy.docx").read_bytes()
    document = (await _file_document(session_factory, settings, data, "bai.docx"))[1]
    deps = make_test_deps(settings, session_factory)
    ctx = await _through_s1(session_factory, deps)
    stage = ExtractReferencesStage(deps)
    assert await stage.run(ctx) == StageResult()
    assert await stage.run(ctx) == StageResult()
    assert await _entries(session_factory, document.id) == []
    codes = [w["code"] for w in await _parse_warnings(session_factory, document.id)]
    assert codes == ["LEGACY_ENCODING", "NO_REFERENCE_SECTION"]


@pytest.mark.db
async def test_s2_idempotent(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    user, document = await _text_document(
        session_factory, settings, f"[1] {PARSEABLE}\n[2] Nguyễn Văn An (2020), Bài báo."
    )
    stub = GrobidStub()
    deps = make_test_deps(settings, session_factory, transport=stub.transport)
    ctx = await _through_s1(session_factory, deps)
    stage = ExtractReferencesStage(deps)
    await stage.run(ctx)
    first = [(e.id, e.raw_text) for e in await _entries(session_factory, document.id)]
    assert len(first) == 2
    await stage.run(ctx)
    # Lần chạy mới trên cùng tài liệu dùng lại mục đã có (QĐ-13).
    async with session_factory() as session:
        await create_run(session, document_id=document.id, user_id=user.id, config={})
        await session.commit()
    second_ctx = await _through_s1(session_factory, deps)
    assert second_ctx.run_id != ctx.run_id
    assert await stage.run(second_ctx) == StageResult()
    assert [(e.id, e.raw_text) for e in await _entries(session_factory, document.id)] == first
    assert len(stub.citation_requests) == 1
    async with session_factory() as session:
        total = await session.scalar(select(func.count()).select_from(ReferenceEntry))
    assert total == 2


@pytest.mark.db
async def test_lost_lease_writes_nothing(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    document = (await _text_document(session_factory, settings, f"[1] {PARSEABLE}"))[1]
    deps = make_test_deps(settings, session_factory)
    ctx = await _through_s1(session_factory, deps)
    with pytest.raises(LeaseLostError):
        await ExtractReferencesStage(deps).run(stage_context(ctx.run_id, stage=StageName.S2))
    assert await _entries(session_factory, document.id) == []
