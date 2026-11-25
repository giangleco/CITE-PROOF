"""V2 — W5 trên top-k chunk toàn văn đã truy xuất ở S7; không có toàn văn thì như V1.

Toàn văn có chunk nhưng S7 không truy xuất được chunk nào → `EVIDENCE_INCONCLUSIVE` (gắn cờ)
mà không gọi LLM (spec §7.8).
"""

from citeproof.core.enums import ContentLevelUsed, UnverifiableReason, Verifier
from citeproof.modules.retrieval.retriever import load_retrieved
from citeproof.modules.verification.verifiers.base import (
    LLMVerifier,
    VerificationContext,
    VerificationOutcome,
    deterministic,
    load_chunks,
)

__all__ = ["RagVerifier"]


class RagVerifier(LLMVerifier):
    variant = Verifier.V2

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]:
        full = vc.sources.full_text
        if full is None:
            return [await self._on_source(vc, vc.sources.abstract)]
        async with self._deps.session_factory() as session:
            chunk_ids = await load_retrieved(
                session,
                run_id=vc.info.run_id,
                claim_id=vc.pair.claim_id,
                source_file_id=full.source_file_id,
                k=vc.info.config.retrieval_k,
            )
            chunks = await load_chunks(session, chunk_ids)
        if not chunks:
            return [
                deterministic(
                    vc.info,
                    UnverifiableReason.EVIDENCE_INCONCLUSIVE,
                    self.variant,
                    content_level=ContentLevelUsed.FULL_TEXT,
                )
            ]
        return [await self._judge(vc, source=full, chunks=chunks)]
