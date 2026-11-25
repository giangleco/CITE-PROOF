"""Quy tắc gộp V3 (`07a` §4.4.3)."""

import uuid

import pytest

from citeproof.core.enums import (
    ChunkRegion,
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    UnverifiableReason,
)
from citeproof.modules.verification.aggregation import aggregate
from citeproof.modules.verification.explanations import contains_forbidden
from citeproof.modules.verification.rules import Outcome, ValidatedEvidence

S = ClaimStatus
CHUNK = uuid.uuid4()


def _ev(chunk: uuid.UUID = CHUNK, start: int = 0, end: int = 5) -> ValidatedEvidence:
    return ValidatedEvidence(
        chunk, EvidenceRelation.SUPPORTS, "quote", start, end, ChunkRegion.BODY
    )


def _outcome(
    status: ClaimStatus,
    *,
    reason: UnverifiableReason | None = None,
    evidence: tuple[ValidatedEvidence, ...] = (),
    level: ContentLevelUsed = ContentLevelUsed.FULL_TEXT,
    review: bool = False,
    explanation: str = "giải thích",
    limitations: str | None = None,
    rule: str | None = None,
) -> Outcome:
    return Outcome(status, reason, evidence, level, review, rule, explanation, limitations)


def _unverifiable(reason: UnverifiableReason) -> Outcome:
    return _outcome(S.UNVERIFIABLE, reason=reason)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((S.CONTRADICTED, S.SUPPORTED), S.CONTRADICTED),
        ((S.SUPPORTED, S.SUPPORTED), S.SUPPORTED),
        ((S.UNSUPPORTED, S.UNSUPPORTED), S.UNSUPPORTED),
        ((S.UNVERIFIABLE, S.UNVERIFIABLE), S.UNVERIFIABLE),
        ((S.SUPPORTED, S.UNSUPPORTED), S.PARTIALLY_SUPPORTED),
        ((S.SUPPORTED, S.UNVERIFIABLE), S.PARTIALLY_SUPPORTED),
        ((S.UNSUPPORTED, S.UNVERIFIABLE), S.PARTIALLY_SUPPORTED),
        ((S.PARTIALLY_SUPPORTED, S.PARTIALLY_SUPPORTED), S.PARTIALLY_SUPPORTED),
        ((S.SUPPORTED, S.UNSUPPORTED, S.CONTRADICTED), S.CONTRADICTED),
    ],
)
def test_aggregation_table(statuses: tuple[ClaimStatus, ...], expected: ClaimStatus) -> None:
    outcomes = [
        _unverifiable(UnverifiableReason.EVIDENCE_INCONCLUSIVE)
        if s is S.UNVERIFIABLE
        else _outcome(s)
        for s in statuses
    ]
    result = aggregate(outcomes, language="vi")
    assert result.status is expected
    if expected is S.PARTIALLY_SUPPORTED:
        assert result.needs_user_review is True
    assert (result.unverifiable_reason is None) is (expected is not S.UNVERIFIABLE)


def test_all_unverifiable_keeps_first_reason() -> None:
    result = aggregate(
        [
            _unverifiable(UnverifiableReason.SOURCE_PARSE_FAILED),
            _unverifiable(UnverifiableReason.EVIDENCE_INCONCLUSIVE),
        ],
        language="vi",
    )
    assert result.status is S.UNVERIFIABLE
    assert result.unverifiable_reason is UnverifiableReason.SOURCE_PARSE_FAILED


def test_review_flag_survives_uniform_outcomes() -> None:
    result = aggregate([_outcome(S.SUPPORTED), _outcome(S.SUPPORTED, review=True)], language="en")
    assert result.status is S.SUPPORTED
    assert result.needs_user_review is True
    clean = aggregate([_outcome(S.SUPPORTED), _outcome(S.SUPPORTED)], language="en")
    assert clean.needs_user_review is False


def test_aggregate_evidence_union() -> None:
    other = uuid.uuid4()
    a = _outcome(S.SUPPORTED, evidence=(_ev(CHUNK, 0, 5), _ev(other, 1, 4)))
    b = _outcome(S.SUPPORTED, evidence=(_ev(CHUNK, 0, 5), _ev(CHUNK, 6, 9)))
    result = aggregate([a, b], language="vi")
    assert [(e.source_chunk_id, e.char_start, e.char_end) for e in result.evidence] == [
        (CHUNK, 0, 5),
        (other, 1, 4),
        (CHUNK, 6, 9),
    ]


def test_content_level_is_highest_used_and_texts_are_joined() -> None:
    a = _outcome(
        S.SUPPORTED, level=ContentLevelUsed.ABSTRACT, explanation="một", limitations="giới hạn a"
    )
    b = _outcome(S.UNSUPPORTED, level=ContentLevelUsed.FULL_TEXT, explanation="hai")
    result = aggregate([a, b], language="vi")
    assert result.content_level_used is ContentLevelUsed.FULL_TEXT
    assert result.explanation == "s1: một; s2: hai"
    assert result.limitations == "s1: giới hạn a"


def test_single_outcome_is_returned_unchanged() -> None:
    only = _outcome(S.SUPPORTED, explanation="nguyên văn")
    assert aggregate([only], language="vi") is only


def test_aggregated_explanations_are_free_of_forbidden_words() -> None:
    result = aggregate(
        [_unverifiable(UnverifiableReason.EVIDENCE_INCONCLUSIVE), _outcome(S.SUPPORTED)],
        language="vi",
    )
    assert not contains_forbidden(result.explanation)
