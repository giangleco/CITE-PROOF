"""S7 — truy xuất, W3 (X2) và ghi `retrieval_results` (spec §7.7; T-501, QĐ-24)."""

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import RunStatus, StageName
from citeproof.core.errors import RetryableError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    LLMCall,
    ReferenceCheck,
    RetrievalResult,
    SourceChunk,
    VerificationRun,
)
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import ProviderResponse
from citeproof.modules.retrieval.pairs import load_pairs
from citeproof.modules.retrieval.retriever import Retriever, load_retrieved
from citeproof.modules.retrieval.stage import RetrieveStage
from citeproof.pipeline.config import load_run_info
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.runs import create_run
from citeproof.pipeline.stages import StageRegistry
from tests.modules.retrieval.helpers import ClaimSpec, add_claims, add_source
from tests.modules.sources.test_fetch_stage import _cite, _work
from tests.modules.sources.test_index_stage import _entries
from tests.pipeline.helpers import CountingStage, drain, make_test_deps, pipeline_settings

SessionFactory = async_sessionmaker[AsyncSession]

CHUNKS = [
    "Introduction to the encoder architecture and its training data",
    "The encoder accuracy reaches 95.7% on the benchmark dataset",
    "Residual connections stabilise deep models",
    "Accuracy increases by 12% when the encoder is fine-tuned",
    "Unrelated appendix text about licensing",
]


def _w3(text_en: str) -> ProviderResponse:
    return ProviderResponse(json.dumps({"text_en": text_en, "kept_terms": []}), 10, 5, "end_turn")


def _registry(deps: PipelineDeps) -> StageRegistry:
    return StageRegistry(
        RetrieveStage(deps) if name is StageName.S7 else CountingStage(name) for name in StageName
    )


async def _setup(
    session_factory: SessionFactory,
    claims: Sequence[tuple[str, str]] = (("encoder accuracy 95.7%", "en"),),
    *,
    chunks: Sequence[str] = CHUNKS,
    config: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Một reference, một nguồn toàn văn và các claim; trả về (run, entry, source, claims)."""
    work_id = await _work(session_factory, "Encoder Paper")
    _, run_id = await _cite(session_factory, [work_id])
    [entry] = await _entries(session_factory, run_id)
    source_id = await add_source(session_factory, chunks, work_id=work_id)
    claim_ids = await add_claims(
        session_factory, run_id, [ClaimSpec(text, lang, (entry,)) for text, lang in claims]
    )
    if config is not None:
        async with session_factory() as s:
            await s.execute(
                update(VerificationRun).where(VerificationRun.id == run_id).values(config=config)
            )
            await s.commit()
    return run_id, entry, source_id, claim_ids


async def _rows(session_factory: SessionFactory, run_id: uuid.UUID) -> list[RetrievalResult]:
    async with session_factory() as s:
        return list(
            (
                await s.execute(
                    select(RetrievalResult)
                    .where(RetrievalResult.run_id == run_id)
                    .order_by(
                        RetrievalResult.claim_id,
                        RetrievalResult.query_variant,
                        RetrievalResult.rank,
                    )
                )
            ).scalars()
        )


VI_CLAIM = ("Độ chính xác (accuracy) của encoder đạt 95,7%", "vi")


@pytest.mark.db
async def test_original_variant_only_for_english(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    run_id, _, source_id, [claim_id] = await _setup(session_factory)
    provider = ScriptedProvider([])
    deps = make_test_deps(settings, session_factory, provider=provider)
    await drain(session_factory, _registry(deps), pipeline_settings())
    rows = await _rows(session_factory, run_id)
    assert rows and {r.query_variant for r in rows} == {"original"}
    assert all(r.claim_id == claim_id and r.source_file_id == source_id for r in rows)
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))
    assert all(r.score_lexical is not None and r.score_vector is None for r in rows)
    assert all(r.score_fused == pytest.approx(1 / (60 + r.rank)) for r in rows)
    assert provider.requests == []


@pytest.mark.db
async def test_x2_adds_translated_variant(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    run_id, _, _, _ = await _setup(session_factory, [VI_CLAIM])
    provider = ScriptedProvider([_w3("the encoder accuracy reaches 95.7% on the benchmark")])
    deps = make_test_deps(settings, session_factory, provider=provider)
    await drain(session_factory, _registry(deps), pipeline_settings())
    rows = await _rows(session_factory, run_id)
    assert {r.query_variant for r in rows} == {"original", "translated"}
    assert len(provider.requests) == 1
    by_variant = {
        v: {r.source_chunk_id for r in rows if r.query_variant == v}
        for v in ("original", "translated")
    }
    both = by_variant["original"] & by_variant["translated"]
    assert both
    # Chunk khớp cả hai biến thể có điểm gộp lớn hơn điểm của một biến thể.
    assert max(r.score_fused or 0 for r in rows) > 1 / 61
    async with session_factory() as s:
        calls = (await s.execute(select(LLMCall).where(LLMCall.run_id == run_id))).scalars().all()
    assert [c.stage for c in calls] == ["S7"]


@pytest.mark.db
async def test_translation_dropping_number_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    run_id, *_ = await _setup(session_factory, [("Accuracy tăng 12% khi tinh chỉnh encoder", "vi")])
    provider = ScriptedProvider([_w3("accuracy increases when the encoder is fine-tuned")])
    deps = make_test_deps(settings, session_factory, provider=provider)
    await drain(session_factory, _registry(deps), pipeline_settings())
    rows = await _rows(session_factory, run_id)
    assert rows and {r.query_variant for r in rows} == {"original"}
    assert len(provider.requests) == 1


@pytest.mark.db
@pytest.mark.parametrize("mode", ["x1", "no_llm", "bad_output"])
async def test_translated_variant_skipped(
    session_factory: SessionFactory, clean_db: None, settings: Settings, mode: str
) -> None:
    run_id, *_ = await _setup(
        session_factory,
        [VI_CLAIM],
        config={"variant": "V2", "cross_lingual": "X1"} if mode == "x1" else None,
    )
    bad = ProviderResponse('{"nope": 1}', 1, 1, "end_turn")
    provider = ScriptedProvider([bad, bad] if mode == "bad_output" else [_w3("x 95.7%")])
    deps = make_test_deps(
        settings, session_factory, provider=None if mode == "no_llm" else provider
    )
    await drain(session_factory, _registry(deps), pipeline_settings())
    rows = await _rows(session_factory, run_id)
    assert rows and {r.query_variant for r in rows} == {"original"}
    if mode == "x1":
        assert provider.requests == []


@pytest.mark.db
async def test_retrieval_scoped_to_source_file(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    run_id, _, source_id, _ = await _setup(session_factory)
    other_work = await _work(session_factory, "Other Paper")
    other = await add_source(session_factory, CHUNKS, work_id=other_work)
    deps = make_test_deps(settings, session_factory, provider=ScriptedProvider([]))
    await drain(session_factory, _registry(deps), pipeline_settings())
    rows = await _rows(session_factory, run_id)
    assert rows and {r.source_file_id for r in rows} == {source_id} != {other}
    async with session_factory() as s:
        chunk_sources = set(
            (
                await s.execute(
                    select(SourceChunk.source_file_id).where(
                        SourceChunk.id.in_([r.source_chunk_id for r in rows])
                    )
                )
            ).scalars()
        )
    assert chunk_sources == {source_id}


@pytest.mark.db
async def test_results_written_once_on_resume(
    session_factory: SessionFactory,
    clean_db: None,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Claim A xong ở lần thử 1; claim B gặp lỗi tạm thời. Lần thử 2 không gọi lại W3 cho A.
    run_id, *_ = await _setup(
        session_factory,
        [
            ("Độ chính xác (accuracy) của encoder đạt 95,7%", "vi"),
            ("Residual models được huấn luyện ổn định", "vi"),
        ],
    )
    retrieved: list[uuid.UUID] = []
    original_retrieve = Retriever.retrieve

    async def spy(self: Retriever, session: AsyncSession, **kwargs: Any) -> Any:
        retrieved.append(kwargs["pair"].claim_id)
        return await original_retrieve(self, session, **kwargs)

    monkeypatch.setattr(Retriever, "retrieve", spy)
    provider = ScriptedProvider(
        [
            _w3("the encoder accuracy reaches 95.7%"),
            RetryableError("tạm thời"),
            _w3("residual models are trained stably"),
        ]
    )
    run_settings = pipeline_settings(job_max_attempts=3).model_copy(
        update={"job_backoff_base_seconds": 0.0, "llm_concurrency": 1}
    )
    deps = make_test_deps(
        run_settings.model_copy(
            update={"storage_dir": settings.storage_dir, "database_url": settings.database_url}
        ),
        session_factory,
        provider=provider,
    )
    await drain(session_factory, _registry(deps), run_settings)
    assert len(provider.requests) == 3
    rows = await _rows(session_factory, run_id)
    assert {r.query_variant for r in rows} == {"original", "translated"}
    # Claim A (đã ghi ở lần thử 1) không được truy xuất lại; claim B chạy hai lần.
    claim_a, claim_b = sorted({r.claim_id for r in rows}, key=lambda c: retrieved.index(c))
    assert retrieved.count(claim_a) == 1
    assert retrieved.count(claim_b) == 2
    keys = [(r.claim_id, r.source_chunk_id, r.query_variant) for r in rows]
    assert len(keys) == len(set(keys))
    async with session_factory() as s:
        status = await s.scalar(select(VerificationRun.status).where(VerificationRun.id == run_id))
    assert status == RunStatus.COMPLETED.value


@pytest.mark.db
async def test_retrieval_deterministic(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    # Hai lần chạy của cùng tài liệu trên cùng nguồn cho đúng cùng (chunk_id, rank) mỗi biến thể.
    run_id, entry, _, _ = await _setup(session_factory, [VI_CLAIM])
    async with session_factory() as s:
        info = await load_run_info(s, run_id)
        work_id = await s.scalar(
            select(ReferenceCheck.work_id).where(ReferenceCheck.run_id == run_id)
        )
        second_id = await create_run(
            s, document_id=info.document_id, user_id=info.user_id, config={"variant": "V2"}
        )
        s.add(
            ReferenceCheck(
                run_id=second_id,
                reference_entry_id=entry,
                status="MATCHED",
                work_id=work_id,
                sources_searched=["crossref"],
            )
        )
        await s.commit()
    translation = _w3("the encoder accuracy reaches 95.7%")
    provider = ScriptedProvider([translation, translation])
    deps = make_test_deps(settings, session_factory, provider=provider)
    await drain(session_factory, _registry(deps), pipeline_settings())

    def keys(rows: list[RetrievalResult]) -> list[tuple[str, uuid.UUID, int]]:
        return [(r.query_variant, r.source_chunk_id, r.rank) for r in rows]

    first = keys(await _rows(session_factory, run_id))
    second = keys(await _rows(session_factory, second_id))
    assert first and sorted(first) == sorted(second)


@pytest.mark.db
async def test_skips_unresolved_nonclaims_and_empty_sources(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    work_id = await _work(session_factory, "Encoder Paper")
    _, run_id = await _cite(session_factory, [work_id, None])
    e0, e1 = await _entries(session_factory, run_id)
    await add_source(session_factory, CHUNKS, work_id=work_id)
    await add_claims(
        session_factory,
        run_id,
        [
            ClaimSpec("encoder accuracy", language="en", links=(None,)),
            ClaimSpec("encoder accuracy", language="en", links=(e0,), is_claim=False),
            ClaimSpec("encoder accuracy", language="en", links=(e1,)),
        ],
    )
    deps = make_test_deps(settings, session_factory, provider=ScriptedProvider([]))
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _rows(session_factory, run_id) == []

    # Nguồn không có chunk: không truy xuất.
    work2 = await _work(session_factory, "Empty Paper")
    _, run2 = await _cite(session_factory, [work2])
    [entry2] = await _entries(session_factory, run2)
    await add_source(session_factory, [], work_id=work2)
    await add_claims(session_factory, run2, [ClaimSpec("encoder", "en", (entry2,))])
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _rows(session_factory, run2) == []


@pytest.mark.db
@pytest.mark.parametrize("variant", ["V0", "V1"])
async def test_v0_and_v1_retrieve_nothing(
    session_factory: SessionFactory, clean_db: None, settings: Settings, variant: str
) -> None:
    run_id, *_ = await _setup(session_factory, config={"variant": variant})
    deps = make_test_deps(settings, session_factory, provider=ScriptedProvider([]))
    await drain(session_factory, _registry(deps), pipeline_settings())
    assert await _rows(session_factory, run_id) == []


@pytest.mark.db
async def test_retriever_custom_queries_and_load_retrieved(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    run_id, _, source_id, [claim_id] = await _setup(session_factory)
    deps = make_test_deps(settings, session_factory, provider=ScriptedProvider([]))
    retriever = Retriever(deps)
    async with session_factory() as s:
        info = await load_run_info(s, run_id)
        [pair] = await load_pairs(s, info)
        output = await retriever.retrieve(
            s,
            info=info,
            pair=pair,
            source_file_id=source_id,
            ctx=retriever.call_context(info),
            queries={"subclaim:s1": "residual connections", "subclaim:s2": "licensing appendix"},
        )
    assert set(output.by_variant) == {"subclaim:s1", "subclaim:s2"}
    async with fenced_test_session(session_factory) as s:
        await retriever.write_results(
            s, info=info, pair=pair, source_file_id=source_id, output=output
        )
        await retriever.write_results(  # ghi lại: ON CONFLICT DO NOTHING
            s, info=info, pair=pair, source_file_id=source_id, output=output
        )
    rows = await _rows(session_factory, run_id)
    assert {r.query_variant for r in rows} == {"subclaim:s1", "subclaim:s2"}
    async with session_factory() as s:
        chunks = await load_retrieved(
            s, run_id=run_id, claim_id=claim_id, source_file_id=source_id, k=1
        )
        everything = await load_retrieved(
            s, run_id=run_id, claim_id=claim_id, source_file_id=source_id, k=10
        )
        index = {c.id: c.chunk_index for c in (await s.execute(select(SourceChunk))).scalars()}
    assert [index[c] for c in everything] == [2, 4]
    assert chunks == everything[:1]


@asynccontextmanager
async def fenced_test_session(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s
        await s.commit()
