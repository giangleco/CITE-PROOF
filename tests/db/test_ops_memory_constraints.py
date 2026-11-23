import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import (
    AgentName,
    AgentRunStatus,
    AgentTargetType,
    JobStatus,
    SourceOrigin,
    StageName,
)
from citeproof.db.models import (
    AgentRun,
    Citation,
    CitationLink,
    Claim,
    Document,
    Job,
    LLMCacheEntry,
    LLMCall,
    ReferenceCheck,
    ReferenceEntry,
    RetrievalResult,
    SourceChunk,
    SourceFile,
    VerificationRun,
)
from tests.db.factories import make_claim, make_document, make_reference_entry, make_run
from tests.db.test_domain_constraints import _source


def _cache(key: str, namespace: str, document_id: object = None) -> LLMCacheEntry:
    return LLMCacheEntry(
        cache_key=key,
        namespace=namespace,
        document_id=document_id,
        task="W5",
        model="m",
        prompt_version="p1",
        schema_version="s1",
        response={"ok": True},
    )


def _agent_run(run: VerificationRun, target_id: object) -> AgentRun:
    return AgentRun(
        run_id=run.id,
        agent=AgentName.REFERENCE_RESOLVER.value,
        agent_version="1",
        tool_catalog_version="1",
        target_type=AgentTargetType.REFERENCE_ENTRY.value,
        target_id=target_id,
        status=AgentRunStatus.SUBMITTED.value,
    )


def _chunk(source_id: object) -> SourceChunk:
    return SourceChunk(
        source_file_id=source_id,
        chunk_index=0,
        text="hello world",
        char_start=0,
        char_end=11,
        region="BODY",
        language="en",
    )


@pytest.mark.db
async def test_doc_namespace_cache_requires_document_id(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        s.add(_cache("k1", "eval:D2@abc"))
        await s.flush()
        s.add(_cache("k2", "doc:x"))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_open_access_source_must_be_shared(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        s.add(_source(origin=SourceOrigin.OPEN_ACCESS.value, owner_user_id=doc.user_id))
        with pytest.raises(IntegrityError):
            await s.flush()
    async with session_factory() as s:
        doc = await make_document(s)
        entry = await make_reference_entry(s, doc)
        s.add(_source(origin=SourceOrigin.OPEN_ACCESS.value, reference_entry_id=entry.id))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_job_unique_per_run_stage(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        run = await make_run(s, await make_document(s))
        job = Job(run_id=run.id, stage=StageName.S1.value)
        s.add(job)
        await s.flush()
        await s.refresh(job)
        assert job.status == JobStatus.QUEUED.value
        assert job.attempts == 0
        assert job.run_after is not None
        s.add(Job(run_id=run.id, stage=StageName.S2.value))
        await s.flush()
        s.add(Job(run_id=run.id, stage=StageName.S1.value))
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.db
async def test_delete_document_removes_doc_cache_agent_runs_retrieval(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        claim = await make_claim(s, doc)
        entry = await make_reference_entry(s, doc)
        link = CitationLink(
            citation_id=claim.citation_id,
            reference_entry_id=entry.id,
            link_method="numeric",
        )
        check = ReferenceCheck(run_id=run.id, reference_entry_id=entry.id, status="MATCHED")
        upload = _source(
            origin=SourceOrigin.USER_UPLOAD.value,
            owner_user_id=doc.user_id,
            reference_entry_id=entry.id,
        )
        shared = _source(origin=SourceOrigin.OPEN_ACCESS.value)
        agent_run = _agent_run(run, entry.id)
        s.add_all([link, check, upload, shared, agent_run, _cache("kd", f"doc:{doc.id}", doc.id)])
        s.add(_cache("ke", "eval:D2@abc"))
        await s.flush()
        upload_chunk, shared_chunk = _chunk(upload.id), _chunk(shared.id)
        s.add_all([upload_chunk, shared_chunk])
        await s.flush()
        s.add(Job(run_id=run.id, stage=StageName.S1.value))
        s.add(
            RetrievalResult(
                run_id=run.id,
                claim_id=claim.id,
                source_file_id=upload.id,
                source_chunk_id=upload_chunk.id,
                rank=1,
                score_lexical=0.5,
                score_vector=0.4,
                score_fused=0.45,
                query_variant="original",
            )
        )
        await s.commit()
        doc_id, shared_id = doc.id, shared.id
    async with session_factory() as s:
        await s.delete(await s.get(Document, doc_id))
        await s.commit()
        for model in (
            AgentRun,
            RetrievalResult,
            Job,
            Citation,
            Claim,
            CitationLink,
            ReferenceCheck,
            ReferenceEntry,
            VerificationRun,
        ):
            assert await s.scalar(select(func.count()).select_from(model)) == 0, model
        assert await s.scalar(select(func.count()).select_from(SourceFile)) == 1
        survivor = await s.scalar(select(SourceChunk))
        assert survivor is not None and survivor.source_file_id == shared_id
        keys = (await s.scalars(select(LLMCacheEntry.cache_key))).all()
        assert keys == ["ke"]


@pytest.mark.db
async def test_llm_calls_survive_document_deletion(
    session_factory: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    async with session_factory() as s:
        doc = await make_document(s)
        run = await make_run(s, doc)
        entry = await make_reference_entry(s, doc)
        agent_run = _agent_run(run, entry.id)
        s.add(agent_run)
        await s.flush()
        s.add(
            LLMCall(
                run_id=run.id,
                agent_run_id=agent_run.id,
                stage="S5",
                task="A1",
                provider="anthropic",
                model="m",
                prompt_version="p1",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cache_namespace=f"doc:{doc.id}",
            )
        )
        await s.commit()
        doc_id = doc.id
    async with session_factory() as s:
        await s.delete(await s.get(Document, doc_id))
        await s.commit()
        call = await s.scalar(select(LLMCall))
        assert call is not None
        assert call.run_id is None
        assert call.agent_run_id is None
