"""S5 — lấy bản mở cho các work của lần chạy (spec §7.5, §6.4; T-403, QĐ-07, QĐ-17).

API OpenAlex, Unpaywall và các máy chủ PDF được giả bằng một `httpx.MockTransport` dùng chung
cho `deps.http` (host API cố định) và `deps.source_http` (URL bản mở, resolver giả). Các stage
khác là `CountingStage`; `works` và `reference_checks` được tạo thẳng trong DB như S3 sẽ ghi.
"""

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    AccessStatus,
    ContentLevel,
    DocumentKind,
    ParseMethod,
    ReferenceStatus,
    RefType,
    RunStatus,
    SourceOrigin,
    StageName,
    TitleMatch,
)
from citeproof.core.errors import InternalError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    Document,
    ReferenceCheck,
    ReferenceEntry,
    SourceFile,
    User,
    VerificationRun,
    Work,
)
from citeproof.integrations.oa import HostRateLimits
from citeproof.modules.references.normalize import title_norm
from citeproof.modules.sources.stage_fetch import FetchSourcesStage
from citeproof.pipeline.config import RunScope
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.stages import StageRegistry
from citeproof.storage import LocalStorage
from tests.integrations.metadata.test_metadata_ratelimit import FakeClock
from tests.integrations.oa.fakes import (
    JUMPER_DOI,
    JUMPER_PDF,
    OPEN_PDF,
    html_stream,
    load_oa,
    pdf_stream,
)
from tests.pipeline.helpers import (
    CountingStage,
    drain,
    make_test_deps,
    pipeline_settings,
    stage_context,
)
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]

ARXIV_ID = "1706.03762"
ARXIV_PDF = f"https://export.arxiv.org/pdf/{ARXIV_ID}"
OPEN_SHA = hashlib.sha256(OPEN_PDF).hexdigest()
OTHER_PDF = OPEN_PDF + b"% other\n"


@dataclass
class FakeWeb:
    """OpenAlex, Unpaywall và máy chủ PDF giả; ghi lại host và đường dẫn mọi request."""

    openalex: dict[str, dict[str, Any]] = field(default_factory=dict)
    unpaywall: dict[str, Any] = field(default_factory=dict)
    unpaywall_status: int = 200
    pages: dict[str, Callable[[], httpx.Response]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"]
        path = unquote(request.url.path)
        if host == "api.openalex.org":
            item = self.openalex.get(path.removeprefix("/works/doi:"))
            return httpx.Response(200, json=item) if item else httpx.Response(404)
        if host == "api.unpaywall.org":
            if self.unpaywall_status != 200:
                return httpx.Response(self.unpaywall_status)
            item = self.unpaywall.get(path.removeprefix("/v2/"))
            return httpx.Response(200, json=item) if item else httpx.Response(404)
        page = self.pages.get(f"https://{host}{path}")
        return page() if page is not None else httpx.Response(404, stream=httpx.ByteStream(b""))

    @property
    def visited(self) -> list[str]:
        return [f"{r.headers['host']}{unquote(r.url.path)}" for r in self.requests]

    def deps(self, settings: Settings, session_factory: SessionFactory) -> PipelineDeps:
        return make_test_deps(settings, session_factory, transport=httpx.MockTransport(self))


# --- Dữ liệu ----------------------------------------------------------------------------------


async def _work(
    session_factory: SessionFactory,
    title: str,
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    abstract: str | None = None,
) -> uuid.UUID:
    async with session_factory() as s:
        work = Work(
            title=title,
            title_norm=title_norm(title),
            doi=doi,
            arxiv_id=arxiv_id,
            abstract=abstract,
            metadata_source="crossref",
        )
        s.add(work)
        await s.commit()
        return work.id


async def _cite(
    session_factory: SessionFactory,
    work_ids: Sequence[uuid.UUID | None],
    *,
    user: User | None = None,
    scope: Sequence[int] | None = None,
) -> tuple[User, uuid.UUID]:
    """Một bài trích các work (theo thứ tự) và một lần chạy đã có `reference_checks`.

    `scope`: chỉ số các tài liệu tham khảo thuộc phạm vi (lần chạy con tra lại).
    """
    async with session_factory() as s:
        if user is None:
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
                raw_text=f"[{index + 1}] ...",
                parsed={},
                ref_type=RefType.ARTICLE.value,
                parse_method=ParseMethod.RULE.value,
            )
            for index in range(len(work_ids))
        ]
        s.add_all(entries)
        await s.flush()
        config: dict[str, Any] = {"variant": "V2"}
        if scope is not None:
            config["scope"] = RunScope(
                kind="lookup_retry",
                reference_entry_ids=[entries[i].id for i in scope],
                from_stage=StageName.S3,
            ).model_dump(mode="json")
        run_id = await create_run(s, document_id=document.id, user_id=user.id, config=config)
        for entry, work_id in zip(entries, work_ids, strict=True):
            s.add(
                ReferenceCheck(
                    run_id=run_id,
                    reference_entry_id=entry.id,
                    status=(
                        ReferenceStatus.MATCHED.value
                        if work_id
                        else ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
                    ),
                    work_id=work_id,
                    sources_searched=["crossref"],
                )
            )
        await s.commit()
    return user, run_id


def _registry(stage: FetchSourcesStage) -> StageRegistry:
    return StageRegistry(
        stage if name is StageName.S5 else CountingStage(name) for name in StageName
    )


async def _run_s5(session_factory: SessionFactory, deps: PipelineDeps) -> None:
    await drain(session_factory, _registry(FetchSourcesStage(deps)), pipeline_settings())


async def _sources(session_factory: SessionFactory) -> list[SourceFile]:
    async with session_factory() as s:
        return list(
            (
                await s.execute(
                    select(SourceFile).order_by(SourceFile.work_id, SourceFile.content_level)
                )
            ).scalars()
        )


async def _run_status(session_factory: SessionFactory, run_id: uuid.UUID) -> str:
    async with session_factory() as s:
        run = await s.get(VerificationRun, run_id)
    assert run is not None
    return run.status


# --- Toàn văn ---------------------------------------------------------------------------------


@pytest.mark.db
async def test_open_access_source_shared_and_deduped(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    web = FakeWeb(pages={ARXIV_PDF: pdf_stream})
    deps = web.deps(settings, session_factory)
    work_id = await _work(session_factory, "Attention Is All You Need", arxiv_id=ARXIV_ID)
    _, first_run = await _cite(session_factory, [work_id])
    _, second_run = await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    [source] = await _sources(session_factory)
    assert source.work_id == work_id
    assert source.owner_user_id is None and source.reference_entry_id is None
    assert source.origin == SourceOrigin.OPEN_ACCESS.value
    assert source.access_status == AccessStatus.OPEN_FULL_TEXT.value
    assert source.content_level == ContentLevel.FULL_TEXT.value
    assert source.title_match == TitleMatch.UNKNOWN.value
    assert source.url == ARXIV_PDF and source.license is None
    assert source.sha256 == OPEN_SHA
    assert source.storage_key == f"sources/open/{OPEN_SHA}.pdf"
    assert await LocalStorage(settings.storage_dir).get(source.storage_key) == OPEN_PDF
    assert web.visited == [f"export.arxiv.org/pdf/{ARXIV_ID}"]  # lần chạy sau dùng lại
    for run_id in (first_run, second_run):
        assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value


@pytest.mark.db
async def test_first_successful_option_in_order(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    web = FakeWeb(
        openalex={JUMPER_DOI: load_oa("openalex_doi_jumper")},
        pages={ARXIV_PDF: html_stream, JUMPER_PDF: pdf_stream},
    )
    deps = web.deps(settings, session_factory)
    work_id = await _work(session_factory, "AlphaFold", doi=JUMPER_DOI, arxiv_id=ARXIV_ID)
    await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    [source] = await _sources(session_factory)
    assert (source.url, source.license) == (JUMPER_PDF, "cc-by")
    assert source.access_status == AccessStatus.OPEN_FULL_TEXT.value
    assert web.visited == [
        f"export.arxiv.org/pdf/{ARXIV_ID}",  # arXiv trả trang HTML: bỏ qua
        f"api.openalex.org/works/doi:{JUMPER_DOI}",
        "www.nature.com/articles/s41586-021-03819-2.pdf",
    ]


@pytest.mark.db
async def test_acl_anthology_before_openalex(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    acl_pdf = "https://aclanthology.org/N19-1423.pdf"
    web = FakeWeb(pages={acl_pdf: pdf_stream})
    deps = web.deps(settings, session_factory)
    work_id = await _work(session_factory, "BERT", doi="10.18653/v1/n19-1423")
    await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    [source] = await _sources(session_factory)
    assert (source.url, source.license) == (acl_pdf, "CC BY 4.0")
    assert web.visited == ["aclanthology.org/N19-1423.pdf"]  # không cần gọi OpenAlex


@pytest.mark.db
async def test_metadata_pdf_url_to_private_ip_blocked(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    record = load_oa("openalex_doi_jumper")
    record["best_oa_location"]["pdf_url"] = "http://169.254.169.254/x"
    web = FakeWeb(openalex={JUMPER_DOI: record}, pages={"http://169.254.169.254/x": pdf_stream})
    deps = web.deps(settings, session_factory)
    work_id = await _work(session_factory, "AlphaFold", doi=JUMPER_DOI)
    _, run_id = await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    assert await _sources(session_factory) == []
    assert web.visited == [f"api.openalex.org/works/doi:{JUMPER_DOI}"]
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value


@pytest.mark.db
async def test_unpaywall_only_with_contact_email(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    record = load_oa("openalex_doi_jumper")
    record["best_oa_location"] = None
    web = FakeWeb(
        openalex={JUMPER_DOI: record},
        unpaywall={JUMPER_DOI: load_oa("unpaywall_doi_jumper")},
        pages={JUMPER_PDF: pdf_stream},
    )
    work_id = await _work(session_factory, "AlphaFold", doi=JUMPER_DOI)
    await _cite(session_factory, [work_id])
    await _run_s5(session_factory, web.deps(settings, session_factory))
    assert await _sources(session_factory) == []
    assert not any(r.headers["host"] == "api.unpaywall.org" for r in web.requests)

    with_email = settings.model_copy(update={"contact_email": "ops@citeproof.vn"})
    await _cite(session_factory, [work_id])
    await _run_s5(session_factory, web.deps(with_email, session_factory))
    [source] = await _sources(session_factory)
    assert (source.url, source.license) == (JUMPER_PDF, "cc-by")
    [unpaywall] = [r for r in web.requests if r.headers["host"] == "api.unpaywall.org"]
    assert unpaywall.url.params["email"] == "ops@citeproof.vn"


@pytest.mark.db
async def test_resolver_error_skips_only_that_source(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    record = load_oa("openalex_doi_jumper")
    record["best_oa_location"] = None
    web = FakeWeb(openalex={JUMPER_DOI: record}, unpaywall_status=503)
    deps = web.deps(settings.model_copy(update={"contact_email": "a@b.vn"}), session_factory)
    work_id = await _work(session_factory, "AlphaFold", doi=JUMPER_DOI, abstract="Proteins.")
    _, run_id = await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    [source] = await _sources(session_factory)
    assert source.content_level == ContentLevel.ABSTRACT.value
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value


@pytest.mark.db
async def test_arxiv_downloads_rate_limited_across_works(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    clock = FakeClock()
    times: list[float] = []

    def page() -> httpx.Response:
        times.append(clock.now)
        return pdf_stream()

    second_pdf = "https://export.arxiv.org/pdf/1810.04805"
    web = FakeWeb(pages={ARXIV_PDF: page, second_pdf: page})
    deps = dataclasses.replace(
        web.deps(settings, session_factory),
        source_limits=HostRateLimits(clock=clock, sleep=clock.sleep),
    )
    first = await _work(session_factory, "Attention", arxiv_id=ARXIV_ID)
    second = await _work(session_factory, "BERT", arxiv_id="1810.04805")
    await _cite(session_factory, [first, second])
    await _run_s5(session_factory, deps)

    assert len(await _sources(session_factory)) == 2
    assert sorted(times) == [0.0, 3.0]  # hai work chạy song song vẫn cách nhau 3 s


# --- Abstract ---------------------------------------------------------------------------------


@pytest.mark.db
async def test_abstract_source_created(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    web = FakeWeb()
    deps = web.deps(settings, session_factory)
    title = "Phân loại văn bản tiếng Việt"
    abstract = "Bài báo đề xuất một mô hình phân loại văn bản."
    work_id = await _work(session_factory, title, abstract=abstract)
    await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    [source] = await _sources(session_factory)
    assert source.origin == SourceOrigin.OPEN_ACCESS.value
    assert source.access_status == AccessStatus.OPEN_ABSTRACT.value
    assert source.content_level == ContentLevel.ABSTRACT.value
    assert source.owner_user_id is None and source.reference_entry_id is None
    assert source.url is None and source.license is None
    assert source.storage_key == f"sources/open/{source.sha256}.abstract.json"
    data = await LocalStorage(settings.storage_dir).get(source.storage_key)
    assert hashlib.sha256(data).hexdigest() == source.sha256
    assert json.loads(data) == {"title": title, "abstract": abstract}
    assert web.requests == []  # không DOI, không arXiv: không gọi mạng


@pytest.mark.db
async def test_abstract_and_full_text_for_one_work_then_skipped(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    web = FakeWeb(pages={ARXIV_PDF: pdf_stream})
    deps = web.deps(settings, session_factory)
    work_id = await _work(session_factory, "Attention", arxiv_id=ARXIV_ID, abstract="Attention.")
    await _cite(session_factory, [work_id, work_id])  # hai mục trích cùng một work
    await _run_s5(session_factory, deps)
    await _cite(session_factory, [work_id])
    await _run_s5(session_factory, deps)

    sources = await _sources(session_factory)
    assert [s.content_level for s in sources] == [
        ContentLevel.ABSTRACT.value,
        ContentLevel.FULL_TEXT.value,
    ]
    assert len(web.requests) == 1


# --- Phạm vi ----------------------------------------------------------------------------------


@pytest.mark.db
async def test_scope_limits_works(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    other_pdf = "https://export.arxiv.org/pdf/1810.04805"
    web = FakeWeb(pages={ARXIV_PDF: pdf_stream, other_pdf: lambda: pdf_stream(OTHER_PDF)})
    deps = web.deps(settings, session_factory)
    in_scope = await _work(session_factory, "Attention", arxiv_id=ARXIV_ID, abstract="A.")
    out_of_scope = await _work(session_factory, "BERT", arxiv_id="1810.04805", abstract="B.")
    await _cite(session_factory, [out_of_scope, in_scope, None], scope=[1])
    await _run_s5(session_factory, deps)

    sources = await _sources(session_factory)
    assert {s.work_id for s in sources} == {in_scope}
    assert len(sources) == 2
    assert web.visited == [f"export.arxiv.org/pdf/{ARXIV_ID}"]


@pytest.mark.db
async def test_without_works_does_nothing(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    web = FakeWeb()
    _, run_id = await _cite(session_factory, [None])
    await _run_s5(session_factory, web.deps(settings, session_factory))
    assert await _sources(session_factory) == []
    assert web.requests == []
    assert await _run_status(session_factory, run_id) == RunStatus.COMPLETED.value


async def test_needs_metadata_gateway(settings: Settings) -> None:
    deps = make_test_deps(settings, async_sessionmaker())
    stage = FetchSourcesStage(dataclasses.replace(deps, metadata=None))
    with pytest.raises(InternalError):
        await stage.run(stage_context(uuid.uuid4(), stage=StageName.S5))
    await deps.http.aclose()
