"""V2+A2 — W5 trước, A2 chỉ cho kết quả đáng ngờ trên toàn văn (spec §9, `07a` §4.3.4; T-704).

Mỗi cặp ghi hai dòng: dòng `V2` (kết quả W5 nguyên vẹn, gồm cờ TK-13) và dòng cuối `V2+A2`.
A2 chạy khi bật trong cấu hình lần chạy, W5 ra `UNSUPPORTED`/`PARTIALLY_SUPPORTED` với
`content_level_used = FULL_TEXT`, và còn trong hạn mức chi phí. A2 nhận trạng thái của W5 và
chunk bằng chứng của nó, không nhận lời giải thích. Dòng cuối là phán quyết của A2
(`escalated_from` = dòng `V2`); A2 không cho kết quả (hết ngân sách, không nộp, lỗi) hoặc hạn
mức chặn A2 → dòng cuối là bản sao kết quả W5, gắn cờ. Không leo thang → bản sao kết quả W5.
"""

import dataclasses

from citeproof.core.enums import AgentName, ClaimStatus, ContentLevelUsed, Verifier
from citeproof.modules.verification.verifiers.base import (
    VerificationContext,
    VerificationOutcome,
)
from citeproof.modules.verification.verifiers.v2 import RagVerifier
from citeproof.modules.verification.verifiers.v4_agentic import FirstPass, investigate
from citeproof.pipeline.deps import PipelineDeps

__all__ = ["ESCALATE_STATUSES", "EscalatingVerifier"]

ESCALATE_STATUSES = frozenset({ClaimStatus.UNSUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED})


class EscalatingVerifier:
    variant = Verifier.V2_A2

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps
        self._first = RagVerifier(deps)

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]:
        [first] = await self._first.verify(vc)
        return [first, await self._final(vc, first)]

    def _escalates(self, vc: VerificationContext, first: VerificationOutcome) -> bool:
        cfg = vc.info.config.agents.get(AgentName.EVIDENCE_INVESTIGATOR.value)
        return (
            cfg is not None
            and cfg.enabled
            and self._deps.agents is not None
            and vc.sources.full_text is not None
            and first.outcome.status in ESCALATE_STATUSES
            and first.outcome.content_level_used is ContentLevelUsed.FULL_TEXT
        )

    async def _final(
        self, vc: VerificationContext, first: VerificationOutcome
    ) -> VerificationOutcome:
        copy = dataclasses.replace(first, verifier=self.variant)
        full = vc.sources.full_text
        if full is None or not self._escalates(vc, first):
            return copy
        flagged = dataclasses.replace(
            copy, outcome=dataclasses.replace(first.outcome, needs_user_review=True)
        )
        if vc.llm_budget is not None and not await vc.llm_budget():
            return flagged
        result = await investigate(
            self._deps,
            vc,
            full,
            verifier=self.variant,
            first_pass=FirstPass(
                status=first.outcome.status,
                evidence_chunk_ids=[e.source_chunk_id for e in first.outcome.evidence],
            ),
        )
        if result.outcome is None:
            if result.agent_run_id is None:
                return flagged
            return dataclasses.replace(
                flagged, agent_run_id=result.agent_run_id, escalates_previous=True
            )
        return dataclasses.replace(result.outcome, escalates_previous=True)
