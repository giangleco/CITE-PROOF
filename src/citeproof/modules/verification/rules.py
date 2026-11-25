"""Validator tất định `apply_rules` áp sau mọi Verdict (spec §7.8, sáu bước).

Mã, không phải LLM, gán `content_level_used`, `unverifiable_reason`, `needs_user_review`,
`downgraded_by_rule`. `UNVERIFIABLE` luôn có lý do.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from citeproof.core.enums import (
    ChunkRegion,
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    UnverifiableReason,
    Verifier,
)
from citeproof.modules.verification.explanations import (
    ExplanationLanguage,
    deterministic_explanation,
    downgrade_note,
)
from citeproof.modules.verification.quotes import locate_quote
from citeproof.modules.verification.verdict import Verdict

__all__ = [
    "EVIDENCE_MINIMUM",
    "ChunkInfo",
    "ExplanationLanguage",
    "Outcome",
    "RuleContext",
    "ValidatedEvidence",
    "apply_rules",
    "deterministic_outcome",
]

EVIDENCE_MINIMUM: Mapping[ClaimStatus, frozenset[EvidenceRelation]] = MappingProxyType(
    {
        ClaimStatus.SUPPORTED: frozenset({EvidenceRelation.SUPPORTS}),
        ClaimStatus.PARTIALLY_SUPPORTED: frozenset(
            {EvidenceRelation.SUPPORTS, EvidenceRelation.PARTIALLY_SUPPORTS}
        ),
        ClaimStatus.CONTRADICTED: frozenset({EvidenceRelation.CONTRADICTS}),
        ClaimStatus.UNSUPPORTED: frozenset({EvidenceRelation.RELATED}),
    }
)

_ALWAYS_REVIEW = frozenset({ClaimStatus.PARTIALLY_SUPPORTED, ClaimStatus.CONTRADICTED})
_ABSTAIN_REASON = {
    "TEXT_UNREADABLE": UnverifiableReason.SOURCE_PARSE_FAILED,
    "CONTEXT_INCONCLUSIVE": UnverifiableReason.EVIDENCE_INCONCLUSIVE,
    None: UnverifiableReason.EVIDENCE_INCONCLUSIVE,
}


@dataclass(frozen=True)
class ChunkInfo:
    chunk_id: UUID
    short_id: str
    text: str
    page: int | None
    section: str | None
    region: ChunkRegion


@dataclass(frozen=True)
class ValidatedEvidence:
    source_chunk_id: UUID
    relation: EvidenceRelation
    quote_text: str
    char_start: int
    char_end: int
    region: ChunkRegion


@dataclass(frozen=True)
class Outcome:
    status: ClaimStatus
    unverifiable_reason: UnverifiableReason | None
    evidence: tuple[ValidatedEvidence, ...]
    content_level_used: ContentLevelUsed
    needs_user_review: bool
    downgraded_by_rule: str | None
    explanation: str
    limitations: str | None


@dataclass(frozen=True)
class RuleContext:
    content_level: ContentLevelUsed
    verifier: Verifier
    title_mismatch: bool
    language: ExplanationLanguage


def deterministic_outcome(
    reason: UnverifiableReason,
    *,
    language: ExplanationLanguage,
    content_level: ContentLevelUsed = ContentLevelUsed.NONE,
) -> Outcome:
    return Outcome(
        status=ClaimStatus.UNVERIFIABLE,
        unverifiable_reason=reason,
        evidence=(),
        content_level_used=content_level,
        needs_user_review=reason is UnverifiableReason.EVIDENCE_INCONCLUSIVE,
        downgraded_by_rule=None,
        explanation=deterministic_explanation(reason, language),
        limitations=None,
    )


def _validate_evidence(
    verdict: Verdict, chunks: Mapping[str, ChunkInfo]
) -> tuple[list[ValidatedEvidence], bool]:
    kept: list[ValidatedEvidence] = []
    dropped = False
    for item in verdict.evidence:
        chunk = chunks.get(item.chunk_id)  # bước 1
        if chunk is None:
            dropped = True
            continue
        span = locate_quote(item.quote, chunk.text)  # bước 2
        if span is None:
            dropped = True
            continue
        kept.append(
            ValidatedEvidence(
                source_chunk_id=chunk.chunk_id,
                relation=item.relation,
                quote_text=chunk.text[span[0] : span[1]],
                char_start=span[0],
                char_end=span[1],
                region=chunk.region,
            )
        )
    return kept, dropped


def apply_rules(verdict: Verdict, chunks: Mapping[str, ChunkInfo], rc: RuleContext) -> Outcome:
    status = verdict.status
    reason: UnverifiableReason | None = None
    downgraded: str | None = None
    review = False

    if rc.verifier is Verifier.V0:
        # Closed-book: bỏ các bước bằng chứng, giữ trạng thái, luôn gắn cờ.
        if status is ClaimStatus.UNVERIFIABLE:
            reason = _ABSTAIN_REASON[verdict.abstain_cause]
        return Outcome(
            status=status,
            unverifiable_reason=reason,
            evidence=(),
            content_level_used=ContentLevelUsed.NONE,
            needs_user_review=True,
            downgraded_by_rule=None,
            explanation=verdict.explanation,
            limitations=verdict.limitations,
        )

    evidence, dropped = _validate_evidence(verdict, chunks)
    explanation = verdict.explanation

    minimum = EVIDENCE_MINIMUM.get(status)  # bước 3
    if minimum is not None and not any(e.relation in minimum for e in evidence):
        status = ClaimStatus.UNVERIFIABLE
        reason = UnverifiableReason.EVIDENCE_INCONCLUSIVE
        downgraded = "QUOTE_NOT_VERBATIM" if dropped else "EVIDENCE_REQUIRED"
        review = True
    elif (  # bước 4
        rc.content_level is ContentLevelUsed.ABSTRACT and status is ClaimStatus.UNSUPPORTED
    ):
        status = ClaimStatus.UNVERIFIABLE
        reason = UnverifiableReason.ABSTRACT_ONLY_INSUFFICIENT
        downgraded = "ABSTRACT_RULE"
    elif status is ClaimStatus.UNVERIFIABLE:  # bước 5
        reason = _ABSTAIN_REASON[verdict.abstain_cause]

    if downgraded is not None:
        explanation = f"{explanation} {downgrade_note(downgraded, rc.language)}".strip()

    # bước 6
    if reason is UnverifiableReason.EVIDENCE_INCONCLUSIVE:
        review = True
    if status in _ALWAYS_REVIEW:
        review = True
    if status is not ClaimStatus.UNVERIFIABLE:
        if rc.content_level is ContentLevelUsed.ABSTRACT:
            review = True
        if any(e.region in (ChunkRegion.TABLE, ChunkRegion.FIGURE) for e in evidence):
            review = True
        if status is ClaimStatus.UNSUPPORTED and rc.verifier is Verifier.V2:
            review = True
    if rc.title_mismatch:
        review = True

    return Outcome(
        status=status,
        unverifiable_reason=reason,
        evidence=tuple(evidence),
        content_level_used=rc.content_level,
        needs_user_review=review,
        downgraded_by_rule=downgraded,
        explanation=explanation,
        limitations=verdict.limitations,
    )
