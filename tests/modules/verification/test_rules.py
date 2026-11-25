import unicodedata
from uuid import uuid4

import pytest
from pydantic import ValidationError

from citeproof.core.enums import (
    ChunkRegion,
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    UnverifiableReason,
    Verifier,
)
from citeproof.modules.verification.rules import (
    EVIDENCE_MINIMUM,
    ChunkInfo,
    Outcome,
    RuleContext,
    apply_rules,
    deterministic_outcome,
)
from citeproof.modules.verification.verdict import Verdict, VerdictEvidence

S = ClaimStatus
R = EvidenceRelation
TEXT = "The model reached accuracy 95.7% on the test set. Mô hình ngôn ngữ lớn."


def chunk(region: ChunkRegion = ChunkRegion.BODY, text: str = TEXT) -> ChunkInfo:
    return ChunkInfo(uuid4(), "c1", text, 3, "Results", region)


def rc(
    *,
    level: ContentLevelUsed = ContentLevelUsed.FULL_TEXT,
    verifier: Verifier = Verifier.V1,
    mismatch: bool = False,
    language: str = "en",
) -> RuleContext:
    return RuleContext(level, verifier, mismatch, language)  # type: ignore[arg-type]


def verdict(
    status: ClaimStatus,
    evidence: list[tuple[str, str, EvidenceRelation]] | None = None,
    cause: str | None = None,
) -> Verdict:
    return Verdict(
        status=status,
        evidence=[VerdictEvidence(chunk_id=c, quote=q, relation=r) for c, q, r in evidence or []],
        abstain_cause=cause,  # type: ignore[arg-type]
        explanation="Because.",
    )


GOOD = "accuracy 95.7%"


def check(o: Outcome) -> Outcome:
    if o.status is S.UNVERIFIABLE:
        assert o.unverifiable_reason is not None
    else:
        assert o.unverifiable_reason is None
    return o


def test_supported_valid() -> None:
    ch = chunk()
    o = check(apply_rules(verdict(S.SUPPORTED, [("c1", GOOD, R.SUPPORTS)]), {"c1": ch}, rc()))
    assert o.status is S.SUPPORTED
    assert not o.needs_user_review
    assert o.downgraded_by_rule is None
    ev = o.evidence[0]
    assert TEXT[ev.char_start : ev.char_end] == GOOD == ev.quote_text
    assert ev.source_chunk_id == ch.chunk_id


def test_unknown_chunk_id_dropped() -> None:
    v = verdict(S.SUPPORTED, [("zz", GOOD, R.SUPPORTS), ("c1", GOOD, R.SUPPORTS)])
    o = check(apply_rules(v, {"c1": chunk()}, rc()))
    assert o.status is S.SUPPORTED
    assert len(o.evidence) == 1


def test_all_evidence_dropped_downgrades() -> None:
    v = verdict(S.SUPPORTED, [("c1", "accuracy 99.9%", R.SUPPORTS)])
    o = check(apply_rules(v, {"c1": chunk()}, rc()))
    assert o.status is S.UNVERIFIABLE
    assert o.unverifiable_reason is UnverifiableReason.EVIDENCE_INCONCLUSIVE
    assert o.downgraded_by_rule == "QUOTE_NOT_VERBATIM"
    assert o.needs_user_review
    assert o.explanation.startswith("Because.")
    assert len(o.explanation) > len("Because.")


def test_nfd_quote_accepted_against_nfc_chunk() -> None:
    q = unicodedata.normalize("NFD", "Mô hình ngôn ngữ lớn")
    o = apply_rules(verdict(S.SUPPORTED, [("c1", q, R.SUPPORTS)]), {"c1": chunk()}, rc())
    assert o.status is S.SUPPORTED
    assert o.evidence[0].quote_text == "Mô hình ngôn ngữ lớn"


def test_minimum_table_complete() -> None:
    assert set(EVIDENCE_MINIMUM) == {
        S.SUPPORTED,
        S.PARTIALLY_SUPPORTED,
        S.CONTRADICTED,
        S.UNSUPPORTED,
    }
    assert EVIDENCE_MINIMUM[S.PARTIALLY_SUPPORTED] == {R.SUPPORTS, R.PARTIALLY_SUPPORTS}


@pytest.mark.parametrize(
    ("status", "relation"),
    [
        (S.SUPPORTED, R.RELATED),
        (S.PARTIALLY_SUPPORTED, R.RELATED),
        (S.CONTRADICTED, R.SUPPORTS),
        (S.UNSUPPORTED, R.SUPPORTS),
    ],
)
def test_evidence_minimum_violation(status: ClaimStatus, relation: EvidenceRelation) -> None:
    o = check(apply_rules(verdict(status, [("c1", GOOD, relation)]), {"c1": chunk()}, rc()))
    assert o.status is S.UNVERIFIABLE
    assert o.downgraded_by_rule == "EVIDENCE_REQUIRED"
    assert o.unverifiable_reason is UnverifiableReason.EVIDENCE_INCONCLUSIVE
    assert o.needs_user_review


def test_partially_supports_satisfies_partial() -> None:
    v = verdict(S.PARTIALLY_SUPPORTED, [("c1", GOOD, R.PARTIALLY_SUPPORTS)])
    o = apply_rules(v, {"c1": chunk()}, rc())
    assert o.status is S.PARTIALLY_SUPPORTED
    assert o.needs_user_review


def test_contradicted_flagged() -> None:
    o = apply_rules(verdict(S.CONTRADICTED, [("c1", GOOD, R.CONTRADICTS)]), {"c1": chunk()}, rc())
    assert o.status is S.CONTRADICTED
    assert o.needs_user_review


def test_abstract_unsupported_rule() -> None:
    v = verdict(S.UNSUPPORTED, [("c1", GOOD, R.RELATED)])
    o = check(
        apply_rules(
            v,
            {"c1": chunk()},
            rc(level=ContentLevelUsed.ABSTRACT, verifier=Verifier.V1),
        )
    )
    assert o.status is S.UNVERIFIABLE
    assert o.unverifiable_reason is UnverifiableReason.ABSTRACT_ONLY_INSUFFICIENT
    assert o.downgraded_by_rule == "ABSTRACT_RULE"
    assert not o.needs_user_review
    assert o.content_level_used is ContentLevelUsed.ABSTRACT


def test_abstract_supported_flagged() -> None:
    v = verdict(S.SUPPORTED, [("c1", GOOD, R.SUPPORTS)])
    o = apply_rules(v, {"c1": chunk()}, rc(level=ContentLevelUsed.ABSTRACT))
    assert o.status is S.SUPPORTED
    assert o.needs_user_review


@pytest.mark.parametrize("region", [ChunkRegion.TABLE, ChunkRegion.FIGURE])
def test_table_region_flagged(region: ChunkRegion) -> None:
    v = verdict(S.SUPPORTED, [("c1", GOOD, R.SUPPORTS)])
    o = apply_rules(v, {"c1": chunk(region)}, rc())
    assert o.needs_user_review
    assert o.evidence[0].region is region


def test_v2_unsupported_flagged() -> None:
    v = verdict(S.UNSUPPORTED, [("c1", GOOD, R.RELATED)])
    assert apply_rules(v, {"c1": chunk()}, rc(verifier=Verifier.V2)).needs_user_review
    assert not apply_rules(v, {"c1": chunk()}, rc(verifier=Verifier.V1)).needs_user_review


def test_title_mismatch_flagged() -> None:
    v = verdict(S.SUPPORTED, [("c1", GOOD, R.SUPPORTS)])
    assert apply_rules(v, {"c1": chunk()}, rc(mismatch=True)).needs_user_review


@pytest.mark.parametrize(
    ("cause", "reason", "review"),
    [
        ("TEXT_UNREADABLE", UnverifiableReason.SOURCE_PARSE_FAILED, False),
        ("CONTEXT_INCONCLUSIVE", UnverifiableReason.EVIDENCE_INCONCLUSIVE, True),
        (None, UnverifiableReason.EVIDENCE_INCONCLUSIVE, True),
    ],
)
def test_abstain_cause_mapping(cause: str | None, reason: UnverifiableReason, review: bool) -> None:
    o = check(apply_rules(verdict(S.UNVERIFIABLE, cause=cause), {}, rc()))
    assert o.unverifiable_reason is reason
    assert o.needs_user_review is review
    assert o.downgraded_by_rule is None


def test_abstain_cause_with_supported_is_schema_error() -> None:
    with pytest.raises(ValidationError):
        Verdict.model_validate(
            {
                "status": "SUPPORTED",
                "evidence": [],
                "abstain_cause": "TEXT_UNREADABLE",
                "explanation": "x",
            }
        )


def test_v0_keeps_status_without_evidence() -> None:
    v = verdict(S.SUPPORTED)
    o = apply_rules(v, {}, rc(level=ContentLevelUsed.FULL_TEXT, verifier=Verifier.V0))
    assert o.status is S.SUPPORTED
    assert o.evidence == ()
    assert o.content_level_used is ContentLevelUsed.NONE
    assert o.needs_user_review
    assert o.downgraded_by_rule is None


def test_v0_unverifiable_has_reason() -> None:
    o = check(apply_rules(verdict(S.UNVERIFIABLE), {}, rc(verifier=Verifier.V0)))
    assert o.unverifiable_reason is UnverifiableReason.EVIDENCE_INCONCLUSIVE


def test_unverifiable_always_has_reason() -> None:
    ch = {"c1": chunk()}
    for status in S:
        for rel in R:
            for q in (GOOD, "nope"):
                for level in ContentLevelUsed:
                    for ver in (Verifier.V0, Verifier.V1, Verifier.V2):
                        v = verdict(status, [("c1", q, rel)])
                        check(apply_rules(v, ch, rc(level=level, verifier=ver)))


def test_deterministic_outcome() -> None:
    for reason in UnverifiableReason:
        for lang in ("vi", "en"):
            o = deterministic_outcome(reason, language=lang)  # type: ignore[arg-type]
            assert o.status is S.UNVERIFIABLE
            assert o.unverifiable_reason is reason
            assert o.explanation
            assert o.evidence == ()
            assert o.needs_user_review is (reason is UnverifiableReason.EVIDENCE_INCONCLUSIVE)
            assert o.content_level_used is ContentLevelUsed.NONE
