"""Truy xuất lexical cho một cặp claim-nguồn và ghi `retrieval_results` (spec §7.7, QĐ-24).

`Retriever.retrieve` chỉ đọc (và gọi W3 trước mọi truy vấn DB, nên không giữ kết nối trong khi
chờ LLM); `write_results` ghi, gọi trong `fenced_session` của stage. V3 dùng lại `retrieve`
với `queries` riêng cho từng ý (`subclaim:s<n>`). Không log văn bản claim hay đầu ra LLM.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import LLMTask, StageName
from citeproof.core.errors import CiteProofError, InternalError, LLMOutputError, RetryableError
from citeproof.db.models import RetrievalResult, SourceChunk
from citeproof.llm.types import CacheEntryInvalidError, CacheMissError, CallContext
from citeproof.modules.retrieval.fusion import ScoredChunk, rrf
from citeproof.modules.retrieval.lexical import LexicalHit, build_tsquery, search_chunks
from citeproof.modules.retrieval.pairs import ClaimPair
from citeproof.modules.retrieval.translate import translate_claim
from citeproof.pipeline.config import RunInfo
from citeproof.pipeline.deps import PipelineDeps, call_context

__all__ = ["RetrievalOutput", "Retriever", "load_retrieved"]

logger = logging.getLogger(__name__)

ORIGINAL = "original"
TRANSLATED = "translated"


@dataclass(frozen=True)
class RetrievalOutput:
    fused: list[ScoredChunk] = field(default_factory=list)
    by_variant: dict[str, list[LexicalHit]] = field(default_factory=dict)


class Retriever:
    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    def call_context(self, info: RunInfo) -> CallContext:
        return call_context(self._deps, info, StageName.S7)

    async def _translated(
        self, info: RunInfo, pair: ClaimPair, ctx: CallContext, *, last_attempt: bool
    ) -> str | None:
        """W3 khi X2, có LLM và claim không phải tiếng Anh; lỗi LLM chỉ làm rơi biến thể."""
        llm = self._deps.llm
        if info.config.cross_lingual != "X2" or llm is None or pair.language == "en":
            return None
        try:
            return await translate_claim(
                llm,
                self._deps.prompts,
                info.config.task(LLMTask.CLAIM_TRANSLATE),
                ctx,
                pair.claim_text,
            )
        except LLMOutputError:
            logger.warning("w3_output_invalid", extra={"error_type": "LLMOutputError"})
        except RetryableError as exc:
            if not last_attempt:
                raise
            logger.warning("w3_skipped", extra={"error_type": type(exc).__name__})
        except (InternalError, CacheMissError, CacheEntryInvalidError):
            raise
        except CiteProofError as exc:
            logger.warning("w3_skipped", extra={"error_type": type(exc).__name__})
        return None

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        info: RunInfo,
        pair: ClaimPair,
        source_file_id: UUID,
        ctx: CallContext,
        queries: Mapping[str, str] | None = None,
        last_attempt: bool = False,
    ) -> RetrievalOutput:
        """Top-k mỗi biến thể trong đúng `source_file_id`, rồi gộp RRF (top-k)."""
        if queries is None:
            texts: dict[str, str] = {ORIGINAL: pair.claim_text}
            translated = await self._translated(info, pair, ctx, last_attempt=last_attempt)
            if translated is not None:
                texts[TRANSLATED] = translated
        else:
            texts = dict(queries)
        k = info.config.retrieval_k
        by_variant: dict[str, list[LexicalHit]] = {}
        for variant, text in texts.items():
            tsquery = build_tsquery(text)
            if tsquery is None:
                continue
            by_variant[variant] = await search_chunks(session, source_file_id, tsquery, k)
        return RetrievalOutput(fused=rrf(by_variant, limit=k), by_variant=by_variant)

    async def write_results(
        self,
        session: AsyncSession,
        *,
        info: RunInfo,
        pair: ClaimPair,
        source_file_id: UUID,
        output: RetrievalOutput,
    ) -> None:
        """Một dòng cho mỗi hit của mỗi biến thể; `ON CONFLICT DO NOTHING` nên ghi lại là no-op."""
        everything = rrf(output.by_variant, limit=sum(len(h) for h in output.by_variant.values()))
        fused = {chunk.chunk_id: chunk.score_fused for chunk in everything}
        rows = [
            {
                "run_id": info.run_id,
                "claim_id": pair.claim_id,
                "source_file_id": source_file_id,
                "source_chunk_id": hit.chunk_id,
                "rank": rank,
                "score_lexical": hit.score,
                "score_vector": None,
                "score_fused": fused[hit.chunk_id],
                "query_variant": variant,
            }
            for variant, hits in output.by_variant.items()
            for rank, hit in enumerate(hits, start=1)
        ]
        if rows:
            await session.execute(insert(RetrievalResult).on_conflict_do_nothing(), rows)


async def load_retrieved(
    session: AsyncSession,
    *,
    run_id: UUID,
    claim_id: UUID,
    source_file_id: UUID,
    k: int,
) -> list[UUID]:
    """Chunk đã truy xuất của cặp: `score_fused` tốt nhất qua các biến thể, rồi `chunk_index`."""
    best = func.max(RetrievalResult.score_fused)
    rows = await session.execute(
        select(RetrievalResult.source_chunk_id)
        .join(SourceChunk, SourceChunk.id == RetrievalResult.source_chunk_id)
        .where(
            RetrievalResult.run_id == run_id,
            RetrievalResult.claim_id == claim_id,
            RetrievalResult.source_file_id == source_file_id,
        )
        .group_by(RetrievalResult.source_chunk_id, SourceChunk.chunk_index)
        .order_by(best.desc(), SourceChunk.chunk_index)
        .limit(k)
    )
    return list(rows.scalars())
