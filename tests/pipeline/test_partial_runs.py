"""Lần chạy con: kiểm chứng lại sau khi tải nguồn và tra lại tài liệu lỗi kết nối (FR-34,
T-505, QĐ-30).

Lần chạy cha là cả pipeline trên `vi_text_input.json` (xem `test_end_to_end`): [1] chưa tìm
thấy, [2] khớp và có toàn văn, [3] luận văn. Người dùng tải PDF cho [1] rồi kiểm chứng lại:
chỉ các cặp của [1] được đối chiếu lại, phần còn lại chép từ lần chạy cha ở S9.
"""

import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    ClaimStatus,
    JobStatus,
    ParseMethod,
    ReferenceStatus,
    RefType,
    RunStatus,
    StageName,
    UnverifiableReason,
)
from citeproof.core.errors import InternalError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    CitationLink,
    Claim,
    ClaimVerification,
    Evidence,
    Job,
    ReferenceCheck,
    ReferenceEntry,
    UsageEvent,
    User,
    VerificationRun,
)
from citeproof.llm.providers.base import LLMProvider
from citeproof.modules.reports.merge import merge_parent_results
from citeproof.pipeline.config import RunConfig, RunScope, load_run_info
from citeproof.pipeline.registry import build_registry
from citeproof.pipeline.runs import (
    PARTIAL_SKIPS,
    create_partial_run,
    create_seeded_run,
    resume_run,
)
from citeproof.pipeline.stages import PipelineStage, StageContext, StageRegistry, StageResult
from citeproof.services import reverify, sources
from citeproof.services.report import list_claims, list_references
from citeproof.storage import LocalStorage
from tests.files import titled_pdf
from tests.pipeline.helpers import drain, make_test_deps, pipeline_settings
from tests.pipeline.test_end_to_end import (
    EMAIL,
    FakeServices,
    _run_pipeline,
    _settings,
    routed_llm,
)
from tests.services.helpers import new_run, new_user, run_document_id, set_run

SessionFactory = async_sessionmaker[AsyncSession]
DEVLIN_TITLE = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"


async def _user(session_factory: SessionFactory, email: str = EMAIL) -> User:
    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.email == email))
        assert user is not None
        return user


async def _jobs(session_factory: SessionFactory, run_id: uuid.UUID) -> dict[StageName, Job]:
    async with session_factory() as s:
        rows = (await s.execute(select(Job).where(Job.run_id == run_id))).scalars()
        return {StageName(job.stage): job for job in rows}


async def _not_found_entry(session_factory: SessionFactory, run_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as s:
        entry_id = await s.scalar(
            select(ClaimVerification.reference_entry_id).where(
                ClaimVerification.run_id == run_id,
                ClaimVerification.unverifiable_reason
                == UnverifiableReason.REFERENCE_NOT_FOUND.value,
            )
        )
    assert entry_id is not None
    return entry_id


async def _drain(
    session_factory: SessionFactory,
    settings: Settings,
    provider: LLMProvider | None,
    *,
    services: FakeServices | None = None,
) -> FakeServices:
    services = services or FakeServices()
    deps = make_test_deps(
        settings, session_factory, provider=provider, transport=httpx.MockTransport(services)
    )
    await drain(session_factory, build_registry(deps), pipeline_settings())
    return services


async def _parent_with_upload(
    session_factory: SessionFactory, settings: Settings
) -> tuple[uuid.UUID, uuid.UUID, User]:
    """Lần chạy cha đã xong và một bản PDF tải lên cho tài liệu [1] (chưa tìm thấy)."""
    done = await _run_pipeline(session_factory, settings, routed_llm())
    entry_id = await _not_found_entry(session_factory, done.run_id)
    user = await _user(session_factory)
    async with session_factory() as s:
        await sources.upload_source(
            s,
            LocalStorage(settings.storage_dir),
            settings,
            user=user,
            run_id=done.run_id,
            reference_entry_id=entry_id,
            data=titled_pdf(DEVLIN_TITLE),
        )
        await s.commit()
    return done.run_id, entry_id, user


async def _start_reverify(
    session_factory: SessionFactory, settings: Settings, user: User, run_id: uuid.UUID
) -> uuid.UUID:
    async with session_factory() as s:
        user = await s.merge(user)
        child_id = await reverify.start_reverify_uploads(s, settings, user=user, run_id=run_id)
        await s.commit()
    return child_id


async def _scoped_claim_pairs(session_factory: SessionFactory, entry_id: uuid.UUID) -> int:
    async with session_factory() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(CitationLink)
                .join(Claim, Claim.citation_id == CitationLink.citation_id)
                .where(CitationLink.reference_entry_id == entry_id, Claim.is_claim.is_(True))
            )
            or 0
        )


async def _counts(session_factory: SessionFactory, run_id: uuid.UUID) -> tuple[int, int, int]:
    async with session_factory() as s:
        refs = await s.scalar(
            select(func.count()).select_from(ReferenceCheck).where(ReferenceCheck.run_id == run_id)
        )
        cvs = await s.scalar(
            select(func.count())
            .select_from(ClaimVerification)
            .where(ClaimVerification.run_id == run_id)
        )
        evidence = await s.scalar(
            select(func.count())
            .select_from(Evidence)
            .join(ClaimVerification, ClaimVerification.id == Evidence.claim_verification_id)
            .where(ClaimVerification.run_id == run_id)
        )
    return int(refs or 0), int(cvs or 0), int(evidence or 0)


# --- create_partial_run -----------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("start", [StageName.S6, StageName.S3])
async def test_skipped_stages_marked_done(
    session_factory: SessionFactory, clean_db: None, settings: Settings, start: StageName
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        parent_id = await new_run(s, settings, user)
        await set_run(s, parent_id, status=RunStatus.COMPLETED)
        document_id = await run_document_id(s, parent_id)
        config = RunConfig.resolve(settings, variant=RunConfig().variant).model_copy(
            update={"scope": RunScope(kind="upload", reference_entry_ids=[], from_stage=start)}
        )
        child_id = await create_partial_run(
            s,
            parent_run_id=parent_id,
            user_id=user.id,
            document_id=document_id,
            config=config.model_dump(mode="json"),
            start=start,
        )
        await s.commit()
        child = await s.get(VerificationRun, child_id)
        assert child is not None
        assert child.parent_run_id == parent_id
        assert child.status == RunStatus.QUEUED.value
    jobs = await _jobs(session_factory, child_id)
    skipped = PARTIAL_SKIPS[start]
    assert set(jobs) == skipped | {start}
    for stage in skipped:
        assert jobs[stage].status == JobStatus.DONE.value
        assert jobs[stage].payload == {"skipped": True}
    assert jobs[start].status == JobStatus.QUEUED.value


def test_partial_skips_cover_dependencies() -> None:
    assert PARTIAL_SKIPS[StageName.S3] == {StageName.S1, StageName.S2, StageName.S4}
    assert PARTIAL_SKIPS[StageName.S6] == {
        StageName.S1,
        StageName.S2,
        StageName.S3,
        StageName.S4,
        StageName.S5,
    }


# --- Kiểm chứng lại sau khi tải nguồn ----------------------------------------------------------


@pytest.mark.db
async def test_partial_run_only_reverifies_scoped_pairs(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = _settings(settings)
    parent_id, entry_id, user = await _parent_with_upload(session_factory, settings)
    child_id = await _start_reverify(session_factory, settings, user, parent_id)
    async with session_factory() as s:
        child = await s.get(VerificationRun, child_id)
        assert child is not None
        scope = RunConfig.load(child.config).scope
    assert scope is not None
    assert (scope.kind, scope.reference_entry_ids, scope.from_stage) == (
        "upload",
        [entry_id],
        StageName.S6,
    )

    provider = routed_llm()
    services = await _drain(session_factory, settings, provider)
    expected = await _scoped_claim_pairs(session_factory, entry_id)
    assert expected > 0
    assert len(provider.calls("W5")) == expected
    # S1-S5 bỏ qua: không gọi GROBID cho danh mục, không tra metadata.
    assert not any("api." in visited for visited in services.visited)
    assert not any("processCitationList" in visited for visited in services.visited)
    async with session_factory() as s:
        child = await s.get(VerificationRun, child_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED.value, (child.status, child.error)


@pytest.mark.db
async def test_partial_run_report_complete_after_merge(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = _settings(settings)
    parent_id, entry_id, user = await _parent_with_upload(session_factory, settings)
    child_id = await _start_reverify(session_factory, settings, user, parent_id)
    await _drain(session_factory, settings, routed_llm())

    async with session_factory() as s:
        user = await s.merge(user)
        _, parent_rows = await list_claims(s, user=user, run_id=parent_id)
        _, child_rows = await list_claims(s, user=user, run_id=child_id)
        _, parent_refs = await list_references(s, user=user, run_id=parent_id)
        _, child_refs = await list_references(s, user=user, run_id=child_id)
        index = await s.scalar(
            select(ReferenceEntry.order_index).where(ReferenceEntry.id == entry_id)
        )
        copied_links = await s.scalar(
            select(func.count())
            .select_from(ClaimVerification)
            .where(
                ClaimVerification.run_id == child_id,
                (ClaimVerification.escalated_from.is_not(None))
                | (ClaimVerification.agent_run_id.is_not(None)),
            )
        )
    assert index is not None
    assert len(child_rows) == len(parent_rows)
    assert len(child_refs) == len(parent_refs) == 3
    scoped_parent = [r for r in parent_rows if r.ref_index == index + 1]
    scoped_child = [r for r in child_rows if r.ref_index == index + 1]
    assert scoped_parent and all(r.status == ClaimStatus.UNVERIFIABLE for r in scoped_parent)
    assert any(r.status == ClaimStatus.SUPPORTED for r in scoped_child)
    # Cặp ngoài phạm vi giữ nguyên kết quả của lần chạy cha.
    others = sorted((r.sentence, r.status) for r in parent_rows if r.ref_index != index + 1)
    assert others == sorted((r.sentence, r.status) for r in child_rows if r.ref_index != index + 1)
    assert copied_links == 0
    # Bằng chứng của cặp được chép đi kèm dòng chép.
    _, _, parent_evidence = await _counts(session_factory, parent_id)
    _, _, child_evidence = await _counts(session_factory, child_id)
    assert parent_evidence > 0 and child_evidence > parent_evidence


@pytest.mark.db
async def test_partial_run_not_counted_in_quota(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = _settings(settings)
    parent_id, _, user = await _parent_with_upload(session_factory, settings)

    async def usage() -> int:
        async with session_factory() as s:
            return int(await s.scalar(select(func.count()).select_from(UsageEvent)) or 0)

    before = await usage()
    await _start_reverify(session_factory, settings, user, parent_id)
    assert await usage() == before


class _FailOnce:
    """Bọc một stage thật: lần gọi đầu ném lỗi không thử lại được, sau đó chạy thật."""

    def __init__(self, inner: PipelineStage) -> None:
        self.name = inner.name
        self._inner = inner
        self.calls = 0

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls += 1
        if self.calls == 1:
            raise InternalError("lỗi giả lập")
        return await self._inner.run(ctx)


@pytest.mark.db
async def test_resume_partial_run_after_failure(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    settings = _settings(settings)
    parent_id, _, user = await _parent_with_upload(session_factory, settings)
    child_id = await _start_reverify(session_factory, settings, user, parent_id)

    services = FakeServices()
    deps = make_test_deps(
        settings, session_factory, provider=routed_llm(), transport=httpx.MockTransport(services)
    )
    real = build_registry(deps)
    failing = _FailOnce(real.get(StageName.S8))
    registry = StageRegistry(
        failing if name is StageName.S8 else real.get(name) for name in StageName
    )
    await drain(session_factory, registry, pipeline_settings())
    async with session_factory() as s:
        child = await s.get(VerificationRun, child_id)
        assert child is not None and child.status == RunStatus.FAILED.value
        await resume_run(s, child_id)
        await s.commit()
    await drain(session_factory, registry, pipeline_settings())
    async with session_factory() as s:
        child = await s.get(VerificationRun, child_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED.value, (child.status, child.error)
    counts = await _counts(session_factory, child_id)
    parent_refs, parent_cvs, _ = await _counts(session_factory, parent_id)
    assert counts[:2] == (parent_refs, parent_cvs)

    # S9 chạy lại (ví dụ mất lease sau khi đã ghi): chép lần nữa không thêm dòng nào.
    async with session_factory() as s:
        info = await load_run_info(s, child_id)
        await merge_parent_results(s, info)
        await s.commit()
    assert await _counts(session_factory, child_id) == counts


# --- Tra lại tài liệu lỗi kết nối ---------------------------------------------------------------

_DOIS = [f"10.1000/ref-{n}" for n in range(5)]
_FAILED = {0, 3}


@pytest.mark.db
async def test_lookup_retry_rechecks_only_failed_refs(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        parent_id = await new_run(s, settings, user)
        await set_run(s, parent_id, status=RunStatus.COMPLETED)
        document_id = await run_document_id(s, parent_id)
        for n, doi in enumerate(_DOIS):
            entry = ReferenceEntry(
                document_id=document_id,
                order_index=n,
                raw_text=f"Tác giả {n}. Bài số {n}. doi:{doi}",
                parsed={"title": f"Paper number {n}", "doi": doi, "year": 2020},
                doi_normalized=doi,
                ref_type=RefType.ARTICLE.value,
                parse_method=ParseMethod.RULE.value,
            )
            s.add(entry)
            await s.flush()
            failed = n in _FAILED
            s.add(
                ReferenceCheck(
                    run_id=parent_id,
                    reference_entry_id=entry.id,
                    status=(
                        ReferenceStatus.LOOKUP_FAILED if failed else ReferenceStatus.MATCHED
                    ).value,
                    sources_failed=["crossref"] if failed else [],
                    sources_searched=[] if failed else ["crossref"],
                )
            )
        await s.commit()
        child_id = await reverify.start_lookup_retry(s, settings, user=user, run_id=parent_id)
        await s.commit()

    visited: list[str] = []

    def metadata(request: httpx.Request) -> httpx.Response:
        visited.append(str(request.url).lower())
        return httpx.Response(404)

    deps = make_test_deps(settings, session_factory, transport=httpx.MockTransport(metadata))
    await drain(session_factory, build_registry(deps), pipeline_settings())

    assert visited
    asked = {doi for doi in _DOIS if any(doi in url for url in visited)}
    assert asked == {_DOIS[n] for n in _FAILED}
    async with session_factory() as s:
        child = await s.get(VerificationRun, child_id)
        assert child is not None
        assert child.status in (
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_WARNINGS.value,
        ), (child.status, child.error)
        jobs = await _jobs(session_factory, child_id)
        assert jobs[StageName.S4].payload == {"skipped": True}
        _, refs = await list_references(s, user=user, run_id=child_id)
    assert len(refs) == 5
    statuses = {ref.index - 1: ref.status for ref in refs}
    for n in range(5):
        if n in _FAILED:
            assert statuses[n] == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
        else:
            assert statuses[n] == ReferenceStatus.MATCHED


@pytest.mark.db
async def test_seeded_run_rejects_start_in_skipped(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        with pytest.raises(ValueError):
            await create_seeded_run(
                s,
                document_id=uuid.uuid4(),
                user_id=user.id,
                config={},
                start=StageName.S6,
                skipped={StageName.S5, StageName.S6},
            )
