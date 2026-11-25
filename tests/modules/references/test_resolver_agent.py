"""A1 Reference Resolver (spec §9, `07a` §4.5.2; T-408): điều kiện leo thang, hậu xử lý tất định
và tích hợp với S3.

Phần S3 dùng `httpx.MockTransport` trả kết quả tìm kiếm theo truy vấn (chuỗi tra cứu không tìm
thấy, truy vấn của agent thì có) và `ScriptedProvider` cho các lượt agent.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.core.enums import (
    AgentName,
    AgentRunStatus,
    AgentTargetType,
    DocumentKind,
    ParseMethod,
    ReferenceStatus,
    RefType,
    ResolvedBy,
    StageName,
)
from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, ReferenceCheck, ReferenceEntry, User
from citeproof.db.models.agents import AgentRun
from citeproof.integrations.metadata import (
    SOURCES,
    InMemoryMetadataCache,
    MetadataGateway,
    WorkAuthor,
    WorkRecord,
)
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import TurnResponse
from citeproof.modules.references.cascade import CascadeResult, ReferenceEntryView
from citeproof.modules.references.matcher import MatchResult
from citeproof.modules.references.resolver_agent import (
    AgentResolver,
    should_escalate,
    verify_agent_candidate,
)
from citeproof.modules.references.stage_check import CheckReferencesStage
from citeproof.pipeline.config import RunConfig, RunInfo
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.registry import _real_stages
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.stages import StageRegistry
from tests.modules.references.test_meta_tools import TextConnector
from tests.pipeline.helpers import (
    CountingStage,
    drain,
    make_test_deps,
    pipeline_settings,
    stage_context,
)
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]

HE_TITLE = "Deep Residual Learning for Image Recognition"
HE_DOI = "10.1109/cvpr.2016.90"
TYPO_TITLE = "Deep Resdual Lerning for Imag Recogniton"
VI_TITLE = "Học sâu dư cho nhận dạng hình ảnh"
IDENTITY_TITLE = "Identity Mappings in Deep Residual Networks"
MODEL = "claude-sonnet-5-5"
ENABLED = {"reference_resolver": {"enabled": True}}


def _he(**fields: Any) -> WorkRecord:
    base: dict[str, Any] = {
        "source": "crossref",
        "source_id": HE_DOI,
        "title": HE_TITLE,
        "authors": [WorkAuthor(family="He", given="Kaiming"), WorkAuthor(family="Zhang")],
        "year": 2016,
        "venue": "CVPR",
        "doi": HE_DOI,
    }
    return WorkRecord.model_validate(base | fields)


def _ref(**fields: Any) -> ParsedReference:
    base: dict[str, Any] = {"title": TYPO_TITLE, "authors": ["He, K."], "year": 2016}
    return ParsedReference.model_validate(base | fields)


def _prior(status: ReferenceStatus, **fields: Any) -> CascadeResult:
    base: dict[str, Any] = {
        "status": status,
        "record": None,
        "match": None,
        "sources_searched": ("crossref", "openalex", "semantic_scholar"),
        "sources_failed": (),
        "needs_user_review": False,
        "close_call": False,
    }
    return CascadeResult(**(base | fields))


def _entry(ref_type: RefType = RefType.ARTICLE, raw: str = "[1] He…") -> ReferenceEntryView:
    return ReferenceEntryView(id=uuid.uuid4(), raw_text=raw, parsed=_ref(), ref_type=ref_type)


async def _no_sleep(seconds: float) -> None:
    return None


def _gateway(**connectors: TextConnector) -> MetadataGateway:
    full = {name: connectors.get(name) or TextConnector(name) for name in SOURCES}
    return MetadataGateway(full, InMemoryMetadataCache(), {}, breaker_threshold=1, sleep=_no_sleep)


# --- Hậu xử lý tất định (`07a` §4.5.2) -----------------------------------------------------------


def _verified(
    ref: ParsedReference | None, raw: str, cand: WorkRecord
) -> tuple[ReferenceStatus, MatchResult, bool]:
    verdict = verify_agent_candidate(ref, raw, cand)
    assert verdict is not None
    return verdict


def test_verify_typo_title_with_author_and_year_matches() -> None:
    status, match, review = _verified(_ref(), "", _he())
    assert status is ReferenceStatus.MATCHED
    assert match.accepted and match.mismatches == ()
    assert not review


def test_verify_doi_equal_accepts() -> None:
    status, match, _ = _verified(_ref(title="Something else entirely", doi=HE_DOI), "", _he())
    assert match.score == 1.0
    assert status is ReferenceStatus.METADATA_MISMATCH  # tiêu đề lệch vẫn được báo


def test_verify_year_off_by_one_is_mismatch() -> None:
    status, match, review = _verified(_ref(year=2015), "", _he())
    assert status is ReferenceStatus.METADATA_MISMATCH
    assert [m.field for m in match.mismatches] == ["year"]
    assert not review


def test_verify_missing_year_needs_strong_title() -> None:
    status, _, _ = _verified(_ref(title=HE_TITLE, year=None), "", _he())
    assert status is ReferenceStatus.MATCHED
    assert verify_agent_candidate(_ref(title="Deep residual nets image", year=None), "", _he()) is (
        None
    )


def test_verify_translated_title_with_venue_needs_review() -> None:
    status, match, review = _verified(_ref(title=VI_TITLE, venue="CVPR"), "", _he())
    assert status is ReferenceStatus.METADATA_MISMATCH
    assert [m.field for m in match.mismatches] == ["title"]
    assert match.mismatches[0].in_document == VI_TITLE
    assert match.mismatches[0].found == HE_TITLE
    assert review


@pytest.mark.parametrize(
    "fields",
    [
        {"title": VI_TITLE},  # không có venue để đối chiếu
        {"title": VI_TITLE, "venue": "ICML"},
        {"title": VI_TITLE, "venue": "CVPR", "year": 2015},
        {"title": VI_TITLE, "venue": "CVPR", "authors": ["Nguyễn Văn An"]},
        {"title": VI_TITLE, "venue": "CVPR", "doi": "10.1145/other"},
    ],
)
def test_verify_rejects_title_mismatch_without_full_agreement(fields: dict[str, Any]) -> None:
    assert verify_agent_candidate(_ref(**fields), "", _he()) is None


def test_verify_rejects_wrong_work() -> None:
    assert verify_agent_candidate(_ref(), "", _he(title=IDENTITY_TITLE, doi="10.1/x")) is None


def test_verify_wrong_author_rejected() -> None:
    assert verify_agent_candidate(_ref(authors=["Smith, J."]), "", _he()) is None


RAW = "He K, Zhang X, Ren S, Sun J. Deep residual learning for image recognition. Proc. CVPR."


def test_verify_parse_failed_checks_raw_text() -> None:
    status, match, review = _verified(None, RAW, _he())
    assert status is ReferenceStatus.METADATA_MISMATCH
    [year] = match.mismatches
    assert (year.field, year.in_document, year.found) == ("year", None, "2016")
    assert not review
    status, match, _ = _verified(None, RAW.replace("Proc.", "Proc. 2016,"), _he())
    assert status is ReferenceStatus.MATCHED
    assert match.mismatches == ()


def test_verify_parse_failed_empty_fields_use_raw_text() -> None:
    status, _, _ = _verified(ParsedReference(), RAW + " 2016", _he())
    assert status is ReferenceStatus.MATCHED


def test_verify_parse_failed_doi_in_raw() -> None:
    raw = "Some garbled text https://doi.org/10.1109/CVPR.2016.90"
    status, match, _ = _verified(None, raw, _he())
    assert match.score == 1.0
    assert status is ReferenceStatus.METADATA_MISMATCH  # tiêu đề, tác giả, năm không có


def test_verify_parse_failed_rejects_unrelated() -> None:
    assert verify_agent_candidate(None, RAW, _he(title=IDENTITY_TITLE, doi=None)) is None
    # Tiêu đề có nhưng không có tác giả lẫn năm.
    raw = "Deep residual learning for image recognition."
    assert verify_agent_candidate(None, raw, _he()) is None


# --- Điều kiện leo thang ---------------------------------------------------------------------


def test_should_escalate_conditions() -> None:
    gateway = _gateway()
    entry = _entry()
    assert should_escalate(entry, _prior(ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES), gateway)
    assert should_escalate(
        entry, _prior(ReferenceStatus.PARSE_FAILED, sources_searched=()), gateway
    )
    assert should_escalate(
        entry, _prior(ReferenceStatus.MATCHED, close_call=True, needs_user_review=True), gateway
    )
    for status in (
        ReferenceStatus.MATCHED,
        ReferenceStatus.METADATA_MISMATCH,
        ReferenceStatus.LOOKUP_FAILED,
        ReferenceStatus.UNSUPPORTED_TYPE,
    ):
        assert not should_escalate(entry, _prior(status), gateway)


def test_should_escalate_never_for_web() -> None:
    prior = _prior(ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES)
    assert not should_escalate(_entry(RefType.WEB), prior, _gateway())


async def test_should_escalate_never_when_all_sources_open() -> None:
    error = ExternalServiceError("lỗi", code="METADATA_HTTP_ERROR")
    failing = {name: TextConnector(name, error) for name in SOURCES}
    gateway = _gateway(**failing)
    prior = _prior(ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES)
    for source in SOURCES[:-1]:
        await gateway.search_text(source, "deep residual")
        assert should_escalate(_entry(), prior, gateway)
    await gateway.search_text(SOURCES[-1], "deep residual")
    assert not should_escalate(_entry(), prior, gateway)


# --- Tích hợp S3 -------------------------------------------------------------------------------

SEARCH_PARAM = {
    "api.crossref.org": "query.bibliographic",
    "api.openalex.org": "search",
    "api.semanticscholar.org": "query",
}


def crossref_item(title: str, doi: str, *, year: int = 2016, venue: str = "CVPR") -> dict[str, Any]:
    return {
        "DOI": doi,
        "title": [title],
        "author": [
            {"given": "Kaiming", "family": "He", "sequence": "first"},
            {"given": "Xiangyu", "family": "Zhang", "sequence": "additional"},
        ],
        "issued": {"date-parts": [[year]]},
        "container-title": [venue],
        "type": "proceedings-article",
    }


@dataclass
class KeyedApis:
    """API metadata giả: tìm Crossref theo truy vấn (chứa khóa → kết quả), mọi thứ khác rỗng."""

    hits: Mapping[str, Sequence[dict[str, Any]]] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        param = SEARCH_PARAM.get(host)
        if param is None:
            return httpx.Response(404)
        query = request.url.params.get(param)
        if query is None:
            return httpx.Response(404)  # tra DOI
        self.queries.append(query)
        items: list[dict[str, Any]] = []
        if host == "api.crossref.org":
            for key, found in self.hits.items():
                if key in query.lower():
                    items.extend(found)
        body = {
            "api.crossref.org": {"status": "ok", "message": {"items": items}},
            "api.openalex.org": {"results": []},
            "api.semanticscholar.org": {"data": []},
        }[host]
        return httpx.Response(200, json=body)


def tool_use(name: str, payload: dict[str, Any], block_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": payload}


def turn(*blocks: dict[str, Any]) -> TurnResponse:
    return TurnResponse(
        content=tuple(blocks),
        stop_reason="tool_use",
        input_tokens=500,
        output_tokens=40,
        model=MODEL,
    )


def search_turn(query: str, block_id: str = "t1") -> TurnResponse:
    return turn(tool_use("meta_search", {"query": query, "year": None, "limit": 5}, block_id))


def submit_turn(
    decision: str, candidate_id: str | None, block_id: str = "t2", note: str = "Same work."
) -> TurnResponse:
    payload = {"decision": decision, "candidate_id": candidate_id, "note": note}
    return turn(tool_use("submit_resolution", payload, block_id))


def _deps(
    settings: Settings,
    session_factory: SessionFactory,
    apis: KeyedApis,
    turns: Sequence[TurnResponse | BaseException] = (),
) -> tuple[PipelineDeps, ScriptedProvider]:
    provider = ScriptedProvider(turns=turns)
    deps = make_test_deps(
        settings, session_factory, provider=provider, transport=httpx.MockTransport(apis)
    )
    return deps, provider


async def _document(
    session_factory: SessionFactory,
    parsed: ParsedReference,
    *,
    ref_type: RefType = RefType.ARTICLE,
    raw_text: str | None = None,
) -> tuple[User, uuid.UUID, uuid.UUID]:
    async with session_factory() as s:
        user = await new_user(s, email=f"{uuid.uuid4().hex[:12]}@b.vn")
        document = Document(
            user_id=user.id,
            kind=DocumentKind.TEXT.value,
            filename="Bài thử",
            mime_type="application/json",
            storage_key=f"documents/{user.id}/x/original.json",
            sha256="0" * 64,
        )
        s.add(document)
        await s.flush()
        entry = ReferenceEntry(
            document_id=document.id,
            order_index=0,
            raw_text=raw_text if raw_text is not None else f"[1] {parsed.title}",
            parsed=parsed.model_dump(mode="json"),
            ref_type=ref_type.value,
            parse_method=ParseMethod.RULE.value,
            doi_normalized=normalize_doi(parsed.doi),
        )
        s.add(entry)
        await s.flush()
        ids = (user, document.id, entry.id)
        await s.commit()
    return ids


async def _run(
    session_factory: SessionFactory,
    deps: PipelineDeps,
    parsed: ParsedReference,
    *,
    agents: Mapping[str, Any] | None = ENABLED,
    ref_type: RefType = RefType.ARTICLE,
    raw_text: str | None = None,
    max_attempts: int = 3,
) -> tuple[uuid.UUID, uuid.UUID, ReferenceCheck]:
    """Tài liệu một mục + lần chạy (A1 bật khi có `agents`), chạy S3 thật với `AgentResolver`."""
    user, document_id, entry_id = await _document(
        session_factory, parsed, ref_type=ref_type, raw_text=raw_text
    )
    config: dict[str, Any] = {"variant": "V2"}
    if agents is not None:
        config["agents"] = dict(agents)
    async with session_factory() as s:
        run_id = await create_run(s, document_id=document_id, user_id=user.id, config=config)
        await s.commit()
    stage = CheckReferencesStage(deps, escalation=AgentResolver(deps))
    registry = StageRegistry(
        stage if name is StageName.S3 else CountingStage(name) for name in StageName
    )
    await drain(session_factory, registry, pipeline_settings(job_max_attempts=max_attempts))
    async with session_factory() as s:
        check = (
            await s.execute(select(ReferenceCheck).where(ReferenceCheck.run_id == run_id))
        ).scalar_one()
    return run_id, entry_id, check


async def _agent_runs(session_factory: SessionFactory, run_id: uuid.UUID) -> list[AgentRun]:
    async with session_factory() as s:
        rows = await s.execute(
            select(AgentRun).where(AgentRun.run_id == run_id).order_by(AgentRun.started_at)
        )
        return list(rows.scalars())


def _tool_results(request_messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    last = request_messages[-1]
    content = last["content"]
    assert isinstance(content, list)
    return [b for b in content if b.get("type") == "tool_result"]


RESCUE = {"deep residual learning": [crossref_item(HE_TITLE, HE_DOI)]}


@pytest.mark.db
async def test_a1_rescues_typo_title(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    apis = KeyedApis(RESCUE)
    deps, provider = _deps(
        settings,
        session_factory,
        apis,
        [
            search_turn("deep residual learning image recognition He"),
            submit_turn("MATCH", "cand_1"),
        ],
    )
    run_id, entry_id, check = await _run(session_factory, deps, _ref())
    assert check.status == ReferenceStatus.MATCHED.value
    assert check.resolved_by == ResolvedBy.AGENT.value
    assert check.agent_run_id is not None
    assert check.work_id is not None
    assert check.mismatches == []
    assert not check.needs_user_review
    assert "crossref" in check.sources_searched
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert agent_run.id == check.agent_run_id
    assert agent_run.agent == AgentName.REFERENCE_RESOLVER.value
    assert agent_run.status == AgentRunStatus.SUBMITTED.value
    assert agent_run.target_type == AgentTargetType.REFERENCE_ENTRY.value
    assert agent_run.target_id == entry_id
    assert agent_run.tool_catalog_version == "v1"
    assert agent_run.final_output is not None
    assert agent_run.final_output["candidate_id"] == "cand_1"
    # Lượt đầu: prompt có chuỗi gốc trong data block không tin cậy, đủ ba công cụ.
    first = provider.turn_requests[0]
    assert [t.name for t in first.tools] == ["meta_search", "meta_get", "submit_resolution"]
    user_text = first.messages[0]["content"]
    assert '<reference untrusted="true">' in user_text
    assert TYPO_TITLE in user_text
    assert "untrusted" in first.system


@pytest.mark.db
async def test_a1_candidate_not_in_session_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    apis = KeyedApis(RESCUE)
    deps, provider = _deps(
        settings,
        session_factory,
        apis,
        [submit_turn("MATCH", "cand_9", "t1"), submit_turn("NO_MATCH", None, "t2")],
    )
    run_id, _, check = await _run(session_factory, deps, _ref())
    [result] = _tool_results(provider.turn_requests[1].messages)
    assert result["is_error"] is True
    assert "UNKNOWN_CANDIDATE_ID" in result["content"]
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert check.work_id is None
    assert not check.needs_user_review
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert check.agent_run_id == agent_run.id
    assert agent_run.final_output is not None
    assert agent_run.final_output["decision"] == "NO_MATCH"


@pytest.mark.db
async def test_a1_match_without_candidate_after_retries_is_no_match(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, _ = _deps(
        settings,
        session_factory,
        KeyedApis(RESCUE),
        [submit_turn("MATCH", None, "t1"), submit_turn("MATCH", None, "t2")],
    )
    _, _, check = await _run(session_factory, deps, _ref())
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value


@pytest.mark.db
async def test_a1_wrong_candidate_keeps_cascade_status(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    apis = KeyedApis({"residual networks": [crossref_item(IDENTITY_TITLE, "10.1007/x")]})
    deps, _ = _deps(
        settings,
        session_factory,
        apis,
        [search_turn("identity mappings residual networks"), submit_turn("MATCH", "cand_1")],
    )
    run_id, _, check = await _run(session_factory, deps, _ref())
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert check.work_id is None
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert check.agent_run_id == agent_run.id
    assert agent_run.final_output == {
        "decision": "MATCH",
        "candidate_id": "cand_1",
        "note": "Same work.",
    }


@pytest.mark.db
async def test_a1_translated_title_review(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, _ = _deps(
        settings,
        session_factory,
        KeyedApis(RESCUE),
        [
            search_turn("deep residual learning for image recognition"),
            submit_turn("MATCH", "cand_1"),
        ],
    )
    parsed = _ref(title=VI_TITLE, venue="CVPR")
    _, _, check = await _run(session_factory, deps, parsed)
    assert check.status == ReferenceStatus.METADATA_MISMATCH.value
    assert check.resolved_by == ResolvedBy.AGENT.value
    assert check.needs_user_review
    assert check.mismatches == [{"field": "title", "in_document": VI_TITLE, "found": HE_TITLE}]


@pytest.mark.db
async def test_a1_parse_failed_checks_raw_text(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    apis = KeyedApis(RESCUE)
    deps, provider = _deps(
        settings,
        session_factory,
        apis,
        [
            search_turn("deep residual learning for image recognition"),
            submit_turn("MATCH", "cand_1"),
        ],
    )
    run_id, _, check = await _run(
        session_factory, deps, ParsedReference(), ref_type=RefType.UNKNOWN, raw_text=RAW
    )
    assert check.status == ReferenceStatus.METADATA_MISMATCH.value
    assert check.resolved_by == ResolvedBy.AGENT.value
    assert check.mismatches == [{"field": "year", "in_document": None, "found": "2016"}]
    assert check.sources_searched == ["crossref", "openalex", "semantic_scholar"]
    assert len(apis.queries) == len(SOURCES)  # chỉ truy vấn của agent; chuỗi tra không tra
    assert RAW in provider.turn_requests[0].messages[0]["content"]
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert check.agent_run_id == agent_run.id


@pytest.mark.db
async def test_a1_close_call_rejected_keeps_top_candidate_flagged(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Hai bản ghi cùng được chấp nhận, sát điểm → cascade MATCHED + needs_user_review → A1 chạy.
    items = [
        crossref_item(HE_TITLE, HE_DOI),
        crossref_item(HE_TITLE + " (extended)", "10.1109/cvpr.2016.91"),
    ]
    apis = KeyedApis({"deep residual learning": items})
    deps, _ = _deps(settings, session_factory, apis, [submit_turn("NO_MATCH", None, "t1")])
    run_id, _, check = await _run(session_factory, deps, _ref(title=HE_TITLE))
    assert check.status == ReferenceStatus.MATCHED.value
    assert check.needs_user_review
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert len(await _agent_runs(session_factory, run_id)) == 1


@pytest.mark.db
async def test_a1_not_run_for_web(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, provider = _deps(settings, session_factory, KeyedApis())
    parsed = _ref(doi="10.9999/web.1")  # có DOI → chuỗi tra chạy và ra "chưa tìm thấy"
    run_id, _, check = await _run(session_factory, deps, parsed, ref_type=RefType.WEB)
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert check.agent_run_id is None
    assert provider.turn_requests == []
    assert await _agent_runs(session_factory, run_id) == []


@pytest.mark.db
async def test_a1_disabled_by_default(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, provider = _deps(settings, session_factory, KeyedApis())
    run_id, _, check = await _run(session_factory, deps, _ref(), agents=None)
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.agent_run_id is None
    assert provider.turn_requests == []
    assert await _agent_runs(session_factory, run_id) == []


@pytest.mark.db
async def test_a1_budget_exhausted_flags_review(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, _ = _deps(
        settings,
        session_factory,
        KeyedApis(),
        [search_turn("first query here", "t1"), search_turn("second query here", "t2")],
    )
    agents = {"reference_resolver": {"enabled": True, "max_steps": 1}}
    run_id, _, check = await _run(session_factory, deps, _ref(), agents=agents)
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert check.needs_user_review
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert agent_run.status == AgentRunStatus.BUDGET_EXHAUSTED.value
    assert check.agent_run_id == agent_run.id


@pytest.mark.db
async def test_a1_retryable_error_propagates_before_last_attempt(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, _ = _deps(settings, session_factory, KeyedApis(), [RetryableError("quá tải")])
    user, document_id, _ = await _document(session_factory, _ref())
    async with session_factory() as s:
        run_id = await create_run(
            s, document_id=document_id, user_id=user.id, config={"agents": ENABLED}
        )
        await s.commit()
    stage = CheckReferencesStage(deps, escalation=AgentResolver(deps))
    with pytest.raises(RetryableError):
        await stage.run(stage_context(run_id, stage=StageName.S3, attempts=1, max_attempts=3))
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert agent_run.status == AgentRunStatus.ERROR.value


@pytest.mark.db
async def test_a1_retryable_error_on_last_attempt_flags_review(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps, _ = _deps(settings, session_factory, KeyedApis(), [RetryableError("quá tải")])
    run_id, _, check = await _run(session_factory, deps, _ref(), max_attempts=1)
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert check.needs_user_review
    [agent_run] = await _agent_runs(session_factory, run_id)
    assert agent_run.status == AgentRunStatus.ERROR.value


@pytest.mark.db
async def test_agent_resolver_returns_none_without_runner(
    session_factory: SessionFactory, settings: Settings
) -> None:
    deps = make_test_deps(settings, session_factory)
    assert deps.agents is None
    resolver = AgentResolver(deps)
    info_prior = _prior(ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES)
    info = RunInfo(
        run_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        parent_run_id=None,
        config=RunConfig.load({"agents": ENABLED}),
        document_language="vi",
    )
    assert await resolver.maybe_resolve(_entry(), info_prior, info, _gateway()) is None


def test_registry_wires_agent_resolver_only_with_agents(
    session_factory: SessionFactory, settings: Settings
) -> None:
    without = _real_stages(make_test_deps(settings, session_factory))[StageName.S3]
    assert isinstance(without, CheckReferencesStage)
    assert without.escalation is None
    provider = ScriptedProvider()
    with_agents = _real_stages(make_test_deps(settings, session_factory, provider=provider))
    stage = with_agents[StageName.S3]
    assert isinstance(stage, CheckReferencesStage)
    assert isinstance(stage.escalation, AgentResolver)
