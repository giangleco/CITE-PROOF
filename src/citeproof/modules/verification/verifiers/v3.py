"""V3 — W4 tách claim thành ≤4 ý, mỗi ý truy xuất và W5 riêng, rồi gộp tất định.

Không có toàn văn thì như V1 (không tách, không gọi W4). Mỗi ý truy xuất trong đúng chunk của
toàn văn với `query_variant = subclaim:<id>` và ghi `retrieval_results`; mọi lời gọi LLM (W4 và
từng W5) đi qua hạn mức chi phí chung (`llm_budget`). Ý không truy xuất được chunk nào cho
`EVIDENCE_INCONCLUSIVE` (không gọi LLM). Hạn mức chạm giữa chừng → cả cặp `PROCESSING_ERROR`
(không gộp kết quả thiếu); `CiteProofError` của một ý lan ra stage. Ghi `retrieval_results`
qua `fenced_session` ngắn.
"""

import dataclasses
import logging
from uuid import UUID

from citeproof.core.enums import ContentLevelUsed, LLMTask, UnverifiableReason, Verifier
from citeproof.core.errors import InternalError, LLMOutputError
from citeproof.modules.retrieval.retriever import Retriever
from citeproof.modules.retrieval.translate import translate_claim
from citeproof.modules.verification.aggregation import aggregate
from citeproof.modules.verification.decompose import SubClaim, decompose
from citeproof.modules.verification.explanations import explanation_language
from citeproof.modules.verification.verifiers.base import (
    NO_MODEL,
    SCHEMA_VERSION,
    LLMVerifier,
    VerificationContext,
    VerificationOutcome,
    deterministic,
    guarded,
    load_chunks,
)
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session

__all__ = ["MultiStepVerifier", "subclaim_variant"]

logger = logging.getLogger(__name__)


def subclaim_variant(sub: SubClaim) -> str:
    return f"subclaim:{sub.id}"


class MultiStepVerifier(LLMVerifier):
    variant = Verifier.V3

    def __init__(self, deps: PipelineDeps) -> None:
        super().__init__(deps)
        self._retriever = Retriever(deps)

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]:
        full = vc.sources.full_text
        if full is None:
            return [await self._on_source(vc, vc.sources.abstract)]
        llm = self._deps.llm
        if llm is None:
            raise InternalError("S8 gọi W4 khi chưa cấu hình LLM")
        if vc.lease is None:
            raise InternalError("V3 cần lease để ghi retrieval_results")
        if vc.llm_budget is not None and not await vc.llm_budget():
            return [deterministic(vc.info, UnverifiableReason.PROCESSING_ERROR, self.variant)]
        subs = await decompose(
            llm,
            self._deps.prompts,
            vc.info.config.task(LLMTask.CLAIM_DECOMPOSE),
            vc.call_ctx,
            vc.pair.claim_text,
            vc.pair.context_text,
        )
        results: list[VerificationOutcome] = []
        for sub in subs:
            results.append(await self._sub_claim(vc, full.source_file_id, sub))
        if any(
            r.outcome.unverifiable_reason is UnverifiableReason.PROCESSING_ERROR for r in results
        ):  # hạn mức chi phí chạm giữa chừng: không gộp kết quả thiếu
            return [deterministic(vc.info, UnverifiableReason.PROCESSING_ERROR, self.variant)]
        language = explanation_language(vc.info.document_language)
        outcome = guarded(aggregate([r.outcome for r in results], language=language), vc.info)
        judged = next((r for r in results if r.model != NO_MODEL), None)
        return [
            VerificationOutcome(
                outcome=outcome,
                model=judged.model if judged else NO_MODEL,
                prompt_version=judged.prompt_version if judged else NO_MODEL,
                schema_version=SCHEMA_VERSION,
                verifier=self.variant,
            )
        ]

    async def _translated(self, vc: VerificationContext, sub: SubClaim) -> str | None:
        """W3 cho truy vấn X2 (qua hạn mức chi phí); chỉ làm rơi biến thể khi lỗi đầu ra."""
        llm = self._deps.llm
        if vc.info.config.cross_lingual != "X2" or llm is None or vc.pair.language == "en":
            return None
        if vc.llm_budget is not None and not await vc.llm_budget():
            return None
        try:
            return await translate_claim(
                llm,
                self._deps.prompts,
                vc.info.config.task(LLMTask.CLAIM_TRANSLATE),
                vc.call_ctx,
                sub.text,
            )
        except LLMOutputError:
            logger.warning("w3_output_invalid", extra={"error_type": "LLMOutputError"})
            return None

    async def _sub_claim(
        self, vc: VerificationContext, source_file_id: UUID, sub: SubClaim
    ) -> VerificationOutcome:
        assert vc.lease is not None
        pair = dataclasses.replace(vc.pair, claim_text=sub.text)
        queries = {subclaim_variant(sub): sub.text}
        translated = await self._translated(vc, sub)
        if translated is not None:
            queries[f"{subclaim_variant(sub)}:translated"] = translated
        async with self._deps.session_factory() as session:
            output = await self._retriever.retrieve(
                session,
                info=vc.info,
                pair=pair,
                source_file_id=source_file_id,
                ctx=vc.call_ctx,
                queries=queries,
            )
        async with fenced_session(self._deps.session_factory, vc.lease) as session:
            await self._retriever.write_results(
                session, info=vc.info, pair=pair, source_file_id=source_file_id, output=output
            )
        async with self._deps.session_factory() as session:
            chunks = await load_chunks(session, [c.chunk_id for c in output.fused])
        if not chunks:
            return deterministic(
                vc.info,
                UnverifiableReason.EVIDENCE_INCONCLUSIVE,
                self.variant,
                content_level=ContentLevelUsed.FULL_TEXT,
            )
        full = vc.sources.full_text
        return await self._judge(dataclasses.replace(vc, pair=pair), source=full, chunks=chunks)
