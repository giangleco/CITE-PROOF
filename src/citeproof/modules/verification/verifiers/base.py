"""Giao thức verifier của S8 và phần dùng chung của V0-V2 (spec §7.8).

Verifier chỉ đọc DB (phiên ngắn, đóng trước khi gọi LLM) và gọi W5; stage ghi kết quả trong
`fenced_session`. Mã, không phải LLM, gán `content_level_used`, `unverifiable_reason`,
`needs_user_review`, `downgraded_by_rule` (qua `apply_rules`). Giải thích chứa từ cấm được thay
bằng văn bản tất định trước khi trả về.
"""

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import (
    ChunkRegion,
    ContentLevel,
    ContentLevelUsed,
    LLMTask,
    SourceOrigin,
    TitleMatch,
    UnverifiableReason,
    Verifier,
)
from citeproof.core.errors import InternalError
from citeproof.db.models import SourceChunk
from citeproof.llm.context import IdMap
from citeproof.llm.types import CallContext
from citeproof.modules.retrieval.pairs import ClaimPair, SourceChoice, SourceRef
from citeproof.modules.verification.context import JudgeInput, RefMeta, judge_task
from citeproof.modules.verification.explanations import explanation_language, safe_texts
from citeproof.modules.verification.judge import judge
from citeproof.modules.verification.rules import (
    ChunkInfo,
    Outcome,
    RuleContext,
    apply_rules,
    deterministic_outcome,
)
from citeproof.modules.verification.verdict import Verdict
from citeproof.pipeline.config import RunInfo
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import ClaimedJob

__all__ = [
    "NO_MODEL",
    "SCHEMA_VERSION",
    "LLMVerifier",
    "PairVerifier",
    "VerificationContext",
    "VerificationOutcome",
    "deterministic",
    "guarded",
    "load_chunks",
    "source_chunk_ids",
    "title_mismatch",
]

# Kết quả tất định (không gọi LLM) ghi `model` và `prompt_version` là "none".
NO_MODEL = "none"
SCHEMA_VERSION = Verdict.SCHEMA_VERSION


@dataclass(frozen=True)
class VerificationContext:
    info: RunInfo
    pair: ClaimPair
    sources: SourceChoice
    ref: RefMeta
    call_ctx: CallContext
    # Hạn mức chi phí (QĐ-32): gọi ngay trước mỗi lời gọi LLM; False → `PROCESSING_ERROR`.
    # Lần gọi đầu của cặp giữ chỗ (có thể chờ cặp khác trả chỗ), các lần sau không giữ thêm.
    # Kết quả tất định (không gọi LLM) không qua hạn mức.
    llm_budget: Callable[[], Awaitable[bool]] | None = None
    # Lease của job S8, cho verifier phải ghi DB giữa chừng (V3 ghi `retrieval_results`).
    lease: ClaimedJob | None = None
    # Lần thử cuối của job S8: A2 coi `RetryableError` là lỗi agent thay vì ném lại.
    is_last_attempt: bool = False


@dataclass(frozen=True)
class VerificationOutcome:
    outcome: Outcome
    model: str
    prompt_version: str
    schema_version: str
    verifier: Verifier
    agent_run_id: UUID | None = None
    escalates_previous: bool = False


class PairVerifier(Protocol):
    variant: Verifier

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]: ...


def guarded(outcome: Outcome, info: RunInfo) -> Outcome:
    """Outcome có giải thích và giới hạn không chứa từ cấm (thay bằng văn bản tất định)."""
    explanation, limitations = safe_texts(
        outcome.status,
        outcome.unverifiable_reason,
        outcome.explanation,
        outcome.limitations,
        explanation_language(info.document_language),
    )
    if (explanation, limitations) == (outcome.explanation, outcome.limitations):
        return outcome
    return dataclasses.replace(outcome, explanation=explanation, limitations=limitations)


def deterministic(
    info: RunInfo,
    reason: UnverifiableReason,
    verifier: Verifier,
    *,
    content_level: ContentLevelUsed = ContentLevelUsed.NONE,
) -> VerificationOutcome:
    """Kết quả `UNVERIFIABLE` không qua LLM (`model`/`prompt_version` = "none")."""
    outcome = deterministic_outcome(
        reason, language=explanation_language(info.document_language), content_level=content_level
    )
    return VerificationOutcome(outcome, NO_MODEL, NO_MODEL, SCHEMA_VERSION, verifier)


async def source_chunk_ids(session: AsyncSession, source_file_id: UUID) -> list[UUID]:
    rows = await session.execute(
        select(SourceChunk.id)
        .where(SourceChunk.source_file_id == source_file_id)
        .order_by(SourceChunk.chunk_index)
    )
    return list(rows.scalars())


async def load_chunks(session: AsyncSession, chunk_ids: Sequence[UUID]) -> tuple[ChunkInfo, ...]:
    """Chunk theo đúng thứ tự `chunk_ids` (xếp hạng), ID ngắn `chunk_1…` theo thứ tự đó."""
    if not chunk_ids:
        return ()
    rows = {
        chunk.id: chunk
        for chunk in (
            await session.execute(select(SourceChunk).where(SourceChunk.id.in_(chunk_ids)))
        ).scalars()
    }
    ids = IdMap("chunk")
    return tuple(
        ChunkInfo(
            chunk_id=chunk_id,
            short_id=ids.add(chunk_id),
            text=rows[chunk_id].text,
            page=rows[chunk_id].page,
            section=rows[chunk_id].section,
            region=ChunkRegion(rows[chunk_id].region),
        )
        for chunk_id in chunk_ids
        if chunk_id in rows
    )


def _content_level(source: SourceRef) -> ContentLevelUsed:
    if source.content_level is ContentLevel.FULL_TEXT:
        return ContentLevelUsed.FULL_TEXT
    return ContentLevelUsed.ABSTRACT


def title_mismatch(source: SourceRef | None) -> bool:
    return (
        source is not None
        and source.origin is SourceOrigin.USER_UPLOAD
        and source.title_match is TitleMatch.MISMATCH
    )


class LLMVerifier:
    """Gọi W5 trên các chunk cho sẵn rồi áp `apply_rules` (dùng chung cho V0, V1, V2)."""

    variant: Verifier

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def _judge(
        self,
        vc: VerificationContext,
        *,
        source: SourceRef | None,
        chunks: tuple[ChunkInfo, ...],
    ) -> VerificationOutcome:
        llm = self._deps.llm
        if llm is None:
            raise InternalError("S8 gọi W5 khi chưa cấu hình LLM")
        if vc.llm_budget is not None and not await vc.llm_budget():
            return deterministic(vc.info, UnverifiableReason.PROCESSING_ERROR, self.variant)
        closed_book = source is None
        content_level = ContentLevelUsed.NONE if source is None else _content_level(source)
        language = explanation_language(vc.info.document_language)
        task: LLMTask = judge_task(closed_book=closed_book)
        inp = JudgeInput(
            ref=vc.ref,
            content_level=content_level,
            claim_text=vc.pair.claim_text,
            claim_language=vc.pair.language,
            context_text=vc.pair.context_text,
            chunks=chunks,
            explanation_language=language,
        )
        result = await judge(
            llm,
            self._deps.prompts,
            vc.info.config.task(task),
            vc.call_ctx,
            inp,
            closed_book=closed_book,
        )
        outcome = apply_rules(
            result.verdict,
            {chunk.short_id: chunk for chunk in chunks},
            RuleContext(
                content_level=content_level,
                verifier=self.variant,
                title_mismatch=title_mismatch(source),
                language=language,
            ),
        )
        return VerificationOutcome(
            outcome=guarded(outcome, vc.info),
            model=result.model,
            prompt_version=result.prompt_version,
            schema_version=SCHEMA_VERSION,
            verifier=self.variant,
        )

    async def _on_source(
        self, vc: VerificationContext, source: SourceRef | None
    ) -> VerificationOutcome:
        """W5 trên mọi chunk của `source` (abstract), theo `chunk_index`."""
        if source is None:
            return deterministic(vc.info, UnverifiableReason.NO_ACCESSIBLE_TEXT, self.variant)
        async with self._deps.session_factory() as session:
            chunks = await load_chunks(
                session, await source_chunk_ids(session, source.source_file_id)
            )
        if not chunks:
            return deterministic(vc.info, UnverifiableReason.SOURCE_PARSE_FAILED, self.variant)
        return await self._judge(vc, source=source, chunks=chunks)
