"""S3 — kiểm tra tài liệu tham khảo (spec §7.3, §6.4; T-301 đến T-304, QĐ-41).

Ba API metadata và trang web được giả bằng một `httpx.MockTransport` dùng chung cho
`deps.http` (host API cố định) và `deps.safe_http` (URL trong bài, resolver giả). Các stage
khác là `CountingStage`; mục tài liệu tham khảo được tạo thẳng trong DB như S2 sẽ ghi.
"""

import asyncio
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.core.enums import (
    DocumentKind,
    DomainFlag,
    JobStatus,
    ParseMethod,
    ReferenceStatus,
    RefType,
    ResolvedBy,
    RunStatus,
    StageName,
)
from citeproof.core.errors import InternalError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Document,
    Job,
    ReferenceCheck,
    ReferenceEntry,
    User,
    VerificationRun,
    Work,
)
from citeproof.integrations.metadata import SOURCES, WorkAuthor, WorkRecord
from citeproof.modules.references.cascade import (
    CascadeResult,
    EscalationResult,
    ReferenceEntryView,
)
from citeproof.modules.references.domain import domain_flag
from citeproof.modules.references.stage_check import CheckReferencesStage
from citeproof.modules.references.works import SAMPLE_METADATA_SOURCE, upsert_work
from citeproof.pipeline.config import RunInfo, RunScope
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.stages import StageRegistry
from citeproof.schemas.report import MismatchView
from citeproof.services import report
from citeproof.services.sample import SAMPLE_METADATA_SOURCE as SERVICE_SAMPLE_SOURCE
from citeproof.services.sample import seed_sample
from tests.modules.references.test_web_check import json_stream, stream_response
from tests.pipeline.helpers import (
    CountingStage,
    drain,
    make_test_deps,
    pipeline_settings,
    stage_context,
)
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]

HOSTS = {
    "api.crossref.org": "crossref",
    "api.openalex.org": "openalex",
    "api.semanticscholar.org": "semantic_scholar",
}
DOI_PREFIX = {
    "crossref": "/works/",
    "openalex": "/works/doi:",
    "semantic_scholar": "/graph/v1/paper/DOI:",
}
HE_TITLE = "Deep Residual Learning for Image Recognition"
HE_DOI = "10.1109/cvpr.2016.90"
LSTM_TITLE = "Long Short-Term Memory"
LSTM_DOI = "10.1162/neco.1997.9.8.1735"
VI_TITLE = "Phân loại văn bản tiếng Việt bằng học sâu"
TITLES = (
    HE_TITLE,
    LSTM_TITLE,
    "Attention Is All You Need",
    "Random Forests",
    "Support-Vector Networks",
)


# --- API giả ----------------------------------------------------------------------------------


def crossref_item(
    title: str, doi: str, *, family: str = "He", given: str = "Kaiming", year: int = 2016
) -> dict[str, Any]:
    return {
        "DOI": doi,
        "title": [title],
        "author": [{"given": given, "family": family, "sequence": "first"}],
        "issued": {"date-parts": [[year]]},
        "container-title": ["Venue"],
        "type": "journal-article",
    }


def openalex_item(
    title: str,
    work_id: str,
    *,
    author: str = "Kaiming He",
    year: int = 2016,
    doi: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": f"https://openalex.org/{work_id}",
        "title": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": author}}],
    }
    if doi is not None:
        item["doi"] = f"https://doi.org/{doi}"
    if field is not None:
        item["primary_topic"] = {"field": {"display_name": field}}
    return item


@dataclass
class FakeApis:
    """Crossref, OpenAlex, Semantic Scholar và trang web giả; ghi lại mọi request."""

    dois: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    searches: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {source: [] for source in SOURCES}
    )
    fail: Callable[[str, httpx.Request], bool] = lambda source, request: False
    raise_error: Exception | None = None
    pages: dict[str, int] = field(default_factory=dict)
    archive: dict[str, Any] | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        source = HOSTS.get(request.url.host)
        if source is None:
            return self._web(request)
        if self.raise_error is not None:
            raise self.raise_error
        if self.fail(source, request):
            return httpx.Response(400)
        path = request.url.path
        prefix = DOI_PREFIX[source]
        if path.startswith(prefix):
            item = self.dois.get((source, unquote(path[len(prefix) :]).lower()))
            if item is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"message": item} if source == "crossref" else item)
        results = self.searches[source]
        body = {
            "crossref": {"status": "ok", "message": {"items": results}},
            "openalex": {"results": results},
            "semantic_scholar": {"data": results},
        }[source]
        return httpx.Response(200, json=body)

    def _web(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        if host == "archive.org" and self.archive is not None:
            return json_stream(self.archive)
        return stream_response(self.pages.get(host, 404))

    @property
    def metadata_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host in HOSTS]

    @property
    def web_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host not in HOSTS]

    def deps(self, settings: Settings, session_factory: SessionFactory) -> PipelineDeps:
        return make_test_deps(settings, session_factory, transport=httpx.MockTransport(self))


# --- Dữ liệu ----------------------------------------------------------------------------------


def _ref(
    title: str | None = HE_TITLE, ref_type: RefType = RefType.ARTICLE, **fields: Any
) -> tuple[ParsedReference, RefType]:
    base: dict[str, Any] = {"title": title, "authors": ["He, K."], "year": 2016}
    return ParsedReference.model_validate(base | fields), ref_type


async def _document(
    session_factory: SessionFactory, refs: Sequence[tuple[ParsedReference, RefType]]
) -> tuple[User, uuid.UUID, list[uuid.UUID]]:
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
        entries = [
            ReferenceEntry(
                document_id=document.id,
                order_index=index,
                raw_text=f"[{index + 1}] {parsed.title or '...'}",
                parsed=parsed.model_dump(mode="json"),
                ref_type=ref_type.value,
                parse_method=ParseMethod.RULE.value,
                doi_normalized=normalize_doi(parsed.doi),
            )
            for index, (parsed, ref_type) in enumerate(refs)
        ]
        s.add_all(entries)
        await s.flush()
        ids = [entry.id for entry in entries]
        document_id = document.id
        await s.commit()
    return user, document_id, ids


async def _new_run(
    session_factory: SessionFactory,
    user: User,
    document_id: uuid.UUID,
    scope: RunScope | None = None,
) -> uuid.UUID:
    config: dict[str, Any] = {"variant": "V2"}
    if scope is not None:
        config["scope"] = scope.model_dump(mode="json")
    async with session_factory() as s:
        run_id = await create_run(s, document_id=document_id, user_id=user.id, config=config)
        await s.commit()
    return run_id


def _registry(stage: CheckReferencesStage) -> StageRegistry:
    return StageRegistry(
        stage if name is StageName.S3 else CountingStage(name) for name in StageName
    )


async def _check(
    session_factory: SessionFactory,
    deps: PipelineDeps,
    refs: Sequence[tuple[ParsedReference, RefType]],
) -> tuple[uuid.UUID, list[ReferenceCheck]]:
    """Tạo tài liệu + lần chạy, chạy hết pipeline (S3 thật); trả run_id và các dòng kiểm tra."""
    user, document_id, _ = await _document(session_factory, refs)
    run_id = await _new_run(session_factory, user, document_id)
    await drain(session_factory, _registry(CheckReferencesStage(deps)), pipeline_settings())
    return run_id, await _checks(session_factory, run_id)


async def _checks(session_factory: SessionFactory, run_id: uuid.UUID) -> list[ReferenceCheck]:
    async with session_factory() as s:
        return list(
            (
                await s.execute(
                    select(ReferenceCheck)
                    .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
                    .where(ReferenceCheck.run_id == run_id)
                    .order_by(ReferenceEntry.order_index)
                )
            ).scalars()
        )


async def _run_row(session_factory: SessionFactory, run_id: uuid.UUID) -> VerificationRun:
    async with session_factory() as s:
        run = await s.get(VerificationRun, run_id)
    assert run is not None
    return run


async def _work(session_factory: SessionFactory, work_id: uuid.UUID | None) -> Work:
    assert work_id is not None
    async with session_factory() as s:
        work = await s.get(Work, work_id)
    assert work is not None
    return work


# --- Trạng thái -------------------------------------------------------------------------------


@pytest.mark.db
async def test_matched_by_doi_writes_check_and_work(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.dois[("crossref", HE_DOI)] = crossref_item(HE_TITLE, HE_DOI)
    deps = api.deps(settings, session_factory)
    run_id, [check] = await _check(session_factory, deps, [_ref(doi="10.1109/CVPR.2016.90")])
    assert (await _run_row(session_factory, run_id)).status == RunStatus.COMPLETED.value
    assert check.status == ReferenceStatus.MATCHED.value
    assert check.sources_searched == ["crossref"]
    assert check.sources_failed == []
    assert check.mismatches == []
    assert check.match_score == Decimal("1")
    assert check.resolved_by == ResolvedBy.CASCADE.value
    assert not check.needs_user_review
    assert check.url_alive is None and check.archive_url is None
    work = await _work(session_factory, check.work_id)
    assert (work.doi, work.title, work.metadata_source) == (HE_DOI, HE_TITLE, "crossref")
    assert work.title_norm == "deep residual learning for image recognition"
    assert work.authors == [{"family": "He", "given": "Kaiming"}]
    assert [r.url.path for r in api.metadata_requests] == [f"/works/{HE_DOI}"]


@pytest.mark.db
async def test_mismatch_shape_rendered_by_report(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["crossref"] = [crossref_item(HE_TITLE, HE_DOI, year=2016)]
    deps = api.deps(settings, session_factory)
    user, document_id, _ = await _document(session_factory, [_ref(year=2017)])
    run_id = await _new_run(session_factory, user, document_id)
    await drain(session_factory, _registry(CheckReferencesStage(deps)), pipeline_settings())
    [check] = await _checks(session_factory, run_id)
    assert check.mismatches == [{"field": "year", "in_document": "2017", "found": "2016"}]
    async with session_factory() as s:
        _, items = await report.list_references(s, user=user, run_id=run_id)
    [item] = items
    assert item.status is ReferenceStatus.METADATA_MISMATCH
    assert item.mismatches == [MismatchView(field="year", in_document="2017", found="2016")]
    assert item.sources_searched == ["crossref"]
    assert item.doi == HE_DOI


@pytest.mark.db
async def test_nfd_reference_matches_nfc_record(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["crossref"] = [
        crossref_item(VI_TITLE, "10.1000/vi.1", family="Nguyễn", given="Văn An", year=2020)
    ]
    deps = api.deps(settings, session_factory)
    nfd = unicodedata.normalize("NFD", VI_TITLE)
    ref = _ref(nfd, authors=[unicodedata.normalize("NFD", "Nguyễn Văn An")], year=2020)
    _, [check] = await _check(session_factory, deps, [ref])
    assert check.status == ReferenceStatus.MATCHED.value
    assert check.mismatches == []


@pytest.mark.db
async def test_not_found_and_lookup_failed_rows(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.fail = lambda source, request: "Long" in str(request.url)
    deps = api.deps(settings, session_factory)
    _, [found_none, failed] = await _check(
        session_factory, deps, [_ref("Random Forests"), _ref(LSTM_TITLE)]
    )
    assert found_none.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert found_none.sources_searched == list(SOURCES)
    assert found_none.work_id is None and found_none.match_score is None
    assert failed.status == ReferenceStatus.LOOKUP_FAILED.value
    assert failed.sources_searched == []
    assert failed.sources_failed == list(SOURCES)


@pytest.mark.db
async def test_parse_failed_without_title_and_doi(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    deps = api.deps(settings, session_factory)
    _, [check] = await _check(session_factory, deps, [_ref(None, authors=[], year=None)])
    assert check.status == ReferenceStatus.PARSE_FAILED.value
    assert check.sources_searched == [] and check.sources_failed == []
    assert api.requests == []


@pytest.mark.db
async def test_web_reference_unsupported_type_with_url_check(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis(pages={"example.org": 200})
    deps = api.deps(settings, session_factory)
    web = _ref("Báo cáo thường niên 2023", RefType.WEB, url="https://example.org/bao-cao?nam=2023")
    _, [check] = await _check(session_factory, deps, [web])
    assert check.status == ReferenceStatus.UNSUPPORTED_TYPE.value
    assert check.url_alive is True
    assert check.archive_url is None  # tra archive.org tắt mặc định (QĐ-42)
    assert api.metadata_requests == []
    [request] = api.web_requests
    assert request.headers["host"] == "example.org"


@pytest.mark.db
async def test_web_reference_archive_lookup_when_enabled(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    snapshot = "http://web.archive.org/web/20240101000000/https://example.org/cu"
    api = FakeApis(
        archive={"archived_snapshots": {"closest": {"available": True, "url": snapshot}}}
    )
    deps = api.deps(settings.model_copy(update={"web_archive_lookup": True}), session_factory)
    web = _ref("Trang đã gỡ", RefType.WEB, url="https://example.org/cu")
    _, [check] = await _check(session_factory, deps, [web])
    assert check.url_alive is False
    assert check.archive_url == snapshot


@pytest.mark.db
async def test_reference_url_to_metadata_ip_blocked(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis(pages={"169.254.169.254": 200})
    deps = api.deps(settings, session_factory)
    web = _ref("Metadata", RefType.WEB, url="http://169.254.169.254/latest")
    _, [check] = await _check(session_factory, deps, [web])
    assert check.status == ReferenceStatus.UNSUPPORTED_TYPE.value
    assert check.url_alive is None
    assert api.requests == []


@pytest.mark.db
async def test_close_call_flags_review(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["crossref"] = [
        crossref_item("Deep residual learning for visual recognition", "10.1000/a.1"),
        crossref_item("Deep Residual Networks for Image Recognition", "10.1000/a.2"),
    ]
    deps = api.deps(settings, session_factory)
    _, [check] = await _check(session_factory, deps, [_ref()])
    assert check.status == ReferenceStatus.METADATA_MISMATCH.value
    assert check.needs_user_review
    assert check.match_score is not None and Decimal("0.9") < check.match_score < 1


# --- works ------------------------------------------------------------------------------------


@pytest.mark.db
async def test_sample_work_overwritten(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    assert SAMPLE_METADATA_SOURCE == SERVICE_SAMPLE_SOURCE
    async with session_factory() as s:
        sample = Work(
            doi=HE_DOI,
            title="Deep residual learning (bài mẫu)",
            title_norm="deep residual learning bai mau",
            authors=["Kaiming He"],
            year=2015,
            metadata_source=SAMPLE_METADATA_SOURCE,
            retrieved_at=datetime.now(UTC),
        )
        s.add(sample)
        await s.commit()
        sample_id = sample.id
    api = FakeApis()
    api.dois[("crossref", HE_DOI)] = crossref_item(HE_TITLE, HE_DOI)
    deps = api.deps(settings, session_factory)
    _, [check] = await _check(session_factory, deps, [_ref(doi=HE_DOI)])
    assert check.work_id == sample_id
    work = await _work(session_factory, sample_id)
    assert (work.metadata_source, work.title, work.year) == ("crossref", HE_TITLE, 2016)
    assert work.authors == [{"family": "He", "given": "Kaiming"}]
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Work)) == 1


@pytest.mark.db
async def test_sample_seed_dois_are_normalized(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Dòng `sample` phải tra được theo DOI chuẩn hóa thì S3 mới ghi đè được (`08` §4.4).
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        dois = (await s.execute(select(Work.doi))).scalars().all()
    assert dois and all(doi is not None and normalize_doi(doi) == doi for doi in dois)


def _record(**fields: Any) -> WorkRecord:
    base: dict[str, Any] = {
        "source": "openalex",
        "source_id": "W1",
        "title": HE_TITLE,
        "authors": [WorkAuthor(family="He", given="Kaiming")],
        "year": 2016,
        "openalex_id": "W1",
        "field": "Computer Science",
        "abstract": "We present a residual learning framework.",
    }
    return WorkRecord.model_validate(base | fields)


@pytest.mark.db
async def test_upsert_work_lookup_order_and_fill(
    session_factory: SessionFactory, clean_db: None
) -> None:
    ttl = timedelta(days=30)
    async with session_factory() as s:
        crossref = _record(
            source="crossref", source_id=HE_DOI, doi=HE_DOI, openalex_id=None, field=None
        )
        first = await upsert_work(s, crossref, ttl=ttl)
        # Bản ghi OpenAlex cùng DOI: dòng còn mới thì giữ nguồn, chỉ lấp cột còn trống.
        second = await upsert_work(s, _record(doi=HE_DOI, title="Khác"), ttl=ttl)
        # Không có DOI: tìm theo OpenAlex ID.
        third = await upsert_work(s, _record(doi=None), ttl=ttl)
        # arXiv ID không phiên bản.
        arxiv = await upsert_work(
            s,
            _record(source="semantic_scholar", openalex_id=None, s2_id="p9", arxiv_id="1512.03385"),
            ttl=ttl,
        )
        same_arxiv = await upsert_work(
            s,
            _record(source="semantic_scholar", openalex_id=None, s2_id=None, arxiv_id="1512.03385"),
            ttl=ttl,
        )
        await s.commit()
        work = await s.get(Work, first)
        count = await s.scalar(select(func.count()).select_from(Work))
    assert first == second == third
    assert arxiv == same_arxiv != first
    assert count == 2
    assert work is not None
    assert (work.metadata_source, work.title) == ("crossref", HE_TITLE)
    assert (work.openalex_id, work.field) == ("W1", "Computer Science")
    assert work.abstract == "We present a residual learning framework."


@pytest.mark.db
async def test_concurrent_upserts_share_one_row(
    session_factory: SessionFactory, clean_db: None
) -> None:
    # Bản ghi chỉ có ID Semantic Scholar (không có ràng buộc duy nhất): giao dịch thứ hai phải
    # chờ advisory lock tới khi giao dịch đầu commit, rồi thấy dòng đã tạo.
    record = _record(source="semantic_scholar", openalex_id=None, s2_id="p42")
    ttl = timedelta(days=30)

    async def upsert() -> uuid.UUID:
        async with session_factory() as s, s.begin():
            return await upsert_work(s, record, ttl=ttl)

    async with session_factory() as first_session, first_session.begin():
        first = await upsert_work(first_session, record, ttl=ttl)
        second_task = asyncio.create_task(upsert())
        await asyncio.sleep(0.3)
        assert not second_task.done()  # đang chờ khóa của giao dịch đầu
    second = await second_task
    async with session_factory() as s:
        count = await s.scalar(select(func.count()).select_from(Work))
    assert first == second
    assert count == 1


@pytest.mark.db
async def test_upsert_work_refreshes_stale_row(
    session_factory: SessionFactory, clean_db: None
) -> None:
    async with session_factory() as s:
        work_id = await upsert_work(s, _record(title="Tiêu đề cũ"), ttl=timedelta(days=30))
        await s.execute(
            Work.__table__.update()
            .where(Work.id == work_id)
            .values(retrieved_at=datetime.now(UTC) - timedelta(days=31))
        )
        again = await upsert_work(s, _record(field=None), ttl=timedelta(days=30))
        await s.commit()
        work = await s.get(Work, work_id)
    assert again == work_id
    assert work is not None
    assert work.title == HE_TITLE
    assert work.field == "Computer Science"  # bản ghi mới thiếu trường thì giữ giá trị cũ
    assert work.retrieved_at > datetime.now(UTC) - timedelta(minutes=5)


# --- Cache, lỗi, phạm vi ------------------------------------------------------------------------


@pytest.mark.db
async def test_second_run_uses_metadata_cache(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["crossref"] = [crossref_item(HE_TITLE, HE_DOI)]
    deps = api.deps(settings, session_factory)
    refs = [_ref(), _ref("Random Forests"), _ref(LSTM_TITLE, doi=LSTM_DOI)]
    user, document_id, _ = await _document(session_factory, refs)
    registry = _registry(CheckReferencesStage(deps))
    first = await _new_run(session_factory, user, document_id)
    await drain(session_factory, registry, pipeline_settings())
    calls = len(api.metadata_requests)
    assert calls > 0
    second = await _new_run(session_factory, user, document_id)
    await drain(session_factory, registry, pipeline_settings())
    assert len(api.metadata_requests) == calls  # T-304: lần chạy thứ hai không gọi lại API

    def summary(checks: list[ReferenceCheck]) -> list[tuple[str, list[str], uuid.UUID | None]]:
        return [(c.status, c.sources_searched, c.work_id) for c in checks]

    first_checks = await _checks(session_factory, first)
    assert summary(first_checks) == summary(await _checks(session_factory, second))
    assert [c.status for c in first_checks] == [
        ReferenceStatus.MATCHED.value,
        ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value,
        ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value,
    ]


@pytest.mark.db
async def test_source_error_is_isolated(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["crossref"] = [
        crossref_item(HE_TITLE, HE_DOI),
        crossref_item(LSTM_TITLE, LSTM_DOI, family="Hochreiter", given="Sepp", year=1997),
    ]
    api.fail = lambda source, request: source == "crossref" and "Long" in str(request.url)
    deps = api.deps(settings, session_factory)
    lstm = _ref(LSTM_TITLE, authors=["Hochreiter, S."], year=1997)
    run_id, [first, second] = await _check(session_factory, deps, [lstm, _ref()])
    assert first.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert first.sources_failed == ["crossref"]
    assert first.sources_searched == ["openalex", "semantic_scholar"]
    assert second.status == ReferenceStatus.MATCHED.value
    async with session_factory() as s:
        job = await s.scalar(select(Job.status).where(Job.run_id == run_id, Job.stage == "S3"))
    assert job == JobStatus.DONE.value


@pytest.mark.db
async def test_programming_error_fails_stage(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis(raise_error=InternalError("lỗi lập trình"))
    deps = api.deps(settings, session_factory)
    run_id, checks = await _check(session_factory, deps, [_ref()])
    assert checks == []  # không bị nuốt thành LOOKUP_FAILED
    async with session_factory() as s:
        job = await s.scalar(select(Job.status).where(Job.run_id == run_id, Job.stage == "S3"))
    assert job == JobStatus.FAILED.value
    run = await _run_row(session_factory, run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None and run.error["type"] == "InternalError"


@pytest.mark.db
async def test_metadata_gateway_missing_is_internal_error(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    deps = FakeApis().deps(settings, session_factory)
    deps.metadata = None
    with pytest.raises(InternalError):
        await CheckReferencesStage(deps).run(stage_context(uuid.uuid4(), stage=StageName.S3))


@pytest.mark.db
async def test_scope_limits_entries(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    deps = api.deps(settings, session_factory)
    user, document_id, ids = await _document(session_factory, [_ref(), _ref("Random Forests")])
    scope = RunScope(kind="lookup_retry", reference_entry_ids=[ids[1]], from_stage=StageName.S3)
    run_id = await _new_run(session_factory, user, document_id, scope)
    await drain(session_factory, _registry(CheckReferencesStage(deps)), pipeline_settings())
    checks = await _checks(session_factory, run_id)
    assert [c.reference_entry_id for c in checks] == [ids[1]]


@pytest.mark.db
async def test_resume_skips_entries_already_checked(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    deps = api.deps(settings, session_factory)
    user, document_id, ids = await _document(session_factory, [_ref(), _ref("Random Forests")])
    run_id = await _new_run(session_factory, user, document_id)
    async with session_factory() as s:
        s.add(
            ReferenceCheck(
                run_id=run_id,
                reference_entry_id=ids[0],
                status=ReferenceStatus.LOOKUP_FAILED.value,
                sources_failed=list(SOURCES),
            )
        )
        await s.commit()
    await drain(session_factory, _registry(CheckReferencesStage(deps)), pipeline_settings())
    first, second = await _checks(session_factory, run_id)
    assert first.status == ReferenceStatus.LOOKUP_FAILED.value
    assert second.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert len(api.metadata_requests) == len(SOURCES)  # chỉ mục thứ hai được tra


# --- Leo thang (A1, Task 25) -------------------------------------------------------------------


@dataclass
class FakeEscalation:
    result: EscalationResult | None
    calls: list[tuple[uuid.UUID, ReferenceStatus, uuid.UUID]] = field(default_factory=list)

    async def maybe_resolve(
        self, entry: ReferenceEntryView, prior: CascadeResult, info: RunInfo
    ) -> EscalationResult | None:
        self.calls.append((entry.id, prior.status, info.run_id))
        return self.result


@pytest.mark.db
async def test_escalation_result_is_written(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    record = WorkRecord(
        source="openalex",
        source_id="W7",
        title="Random forests",
        authors=[WorkAuthor(family="Breiman", given="Leo")],
        year=2001,
        openalex_id="W7",
    )
    agent = CascadeResult(
        status=ReferenceStatus.METADATA_MISMATCH,
        record=record,
        match=None,
        sources_searched=("openalex",),
        sources_failed=(),
        needs_user_review=True,
        close_call=False,
    )
    escalation = FakeEscalation(EscalationResult(agent, None, ResolvedBy.AGENT))
    deps = FakeApis().deps(settings, session_factory)
    user, document_id, ids = await _document(session_factory, [_ref("Rừng ngẫu nhiên")])
    run_id = await _new_run(session_factory, user, document_id)
    stage = CheckReferencesStage(deps, escalation=escalation)
    await drain(session_factory, _registry(stage), pipeline_settings())
    assert escalation.calls == [(ids[0], ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES, run_id)]
    [check] = await _checks(session_factory, run_id)
    assert check.status == ReferenceStatus.METADATA_MISMATCH.value
    assert check.resolved_by == ResolvedBy.AGENT.value
    assert check.needs_user_review
    assert (await _work(session_factory, check.work_id)).openalex_id == "W7"


@pytest.mark.db
async def test_escalation_not_run_keeps_cascade(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    escalation = FakeEscalation(None)
    deps = FakeApis().deps(settings, session_factory)
    user, document_id, _ = await _document(session_factory, [_ref()])
    run_id = await _new_run(session_factory, user, document_id)
    stage = CheckReferencesStage(deps, escalation=escalation)
    await drain(session_factory, _registry(stage), pipeline_settings())
    [check] = await _checks(session_factory, run_id)
    assert check.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
    assert check.resolved_by == ResolvedBy.CASCADE.value


# --- Lĩnh vực (QĐ-41) ---------------------------------------------------------------------------


def test_domain_flag_rule() -> None:
    assert domain_flag(["Medicine"] * 4) is DomainFlag.UNKNOWN  # chưa đủ 5 tài liệu có lĩnh vực
    assert domain_flag(["Medicine"] * 4 + [None, None]) is DomainFlag.UNKNOWN
    assert domain_flag(["Medicine"] * 5) is DomainFlag.OUT_OF_DOMAIN
    assert domain_flag(["Computer Science"] + ["Medicine"] * 3 + ["Biology"]) is (
        DomainFlag.OUT_OF_DOMAIN  # 20% < 25%
    )
    assert domain_flag(["Computer Science"] * 2 + ["Medicine"] * 6) is DomainFlag.IN_SCOPE  # 25%
    assert domain_flag(["computer science"] * 5) is DomainFlag.IN_SCOPE
    assert domain_flag([]) is DomainFlag.UNKNOWN


async def _domain(session_factory: SessionFactory, document_id: uuid.UUID) -> tuple[str, list[Any]]:
    async with session_factory() as s:
        row = (
            await s.execute(
                select(Document.domain_flag, Document.parse_warnings).where(
                    Document.id == document_id
                )
            )
        ).one()
    return row.domain_flag, row.parse_warnings


@pytest.mark.db
async def test_out_of_domain_warning(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["openalex"] = [
        openalex_item(title, f"W{index}", field="Medicine") for index, title in enumerate(TITLES)
    ]
    deps = api.deps(settings, session_factory)
    user, document_id, _ = await _document(session_factory, [_ref(t) for t in TITLES])
    run_id = await _new_run(session_factory, user, document_id)
    await drain(session_factory, _registry(CheckReferencesStage(deps)), pipeline_settings())
    checks = await _checks(session_factory, run_id)
    assert {c.status for c in checks} == {ReferenceStatus.MATCHED.value}
    flag, warnings = await _domain(session_factory, document_id)
    assert flag == DomainFlag.OUT_OF_DOMAIN.value
    assert {"code": "OUT_OF_DOMAIN"} in warnings
    async with session_factory() as s:
        _, items = await report.list_references(s, user=user, run_id=run_id)
        header = (await report.get_overview(s, user=user, run_id=run_id)).header
    assert len(items) == len(TITLES)
    assert [w.code for w in header.warnings] == ["OUT_OF_DOMAIN"]


@pytest.mark.db
async def test_in_scope_document_has_no_domain_warning(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    api = FakeApis()
    api.searches["openalex"] = [
        openalex_item(title, f"W{index}", field="Computer Science")
        for index, title in enumerate(TITLES)
    ]
    deps = api.deps(settings, session_factory)
    user, document_id, _ = await _document(session_factory, [_ref(t) for t in TITLES])
    async with session_factory() as s:
        document = await s.get(Document, document_id)
        assert document is not None
        document.parse_warnings = [
            {"code": "OUT_OF_DOMAIN"},
            {"code": "SCANNED_PAGES", "pages": [2]},
        ]
        await s.commit()
    await _new_run(session_factory, user, document_id)
    await drain(session_factory, _registry(CheckReferencesStage(deps)), pipeline_settings())
    flag, warnings = await _domain(session_factory, document_id)
    assert flag == DomainFlag.IN_SCOPE.value
    assert warnings == [{"code": "SCANNED_PAGES", "pages": [2]}]
