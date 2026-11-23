from citeproof.core.enums import (
    EvidenceRelation,
    JobStatus,
    ReferenceStatus,
    UnverifiableReason,
    Verifier,
)
from citeproof.db.base import enum_check


def test_enum_values_follow_decisions() -> None:
    assert ReferenceStatus.LOOKUP_FAILED == "LOOKUP_FAILED"
    assert UnverifiableReason.EVIDENCE_INCONCLUSIVE == "EVIDENCE_INCONCLUSIVE"
    assert [r.value for r in EvidenceRelation] == [
        "SUPPORTS",
        "PARTIALLY_SUPPORTS",
        "CONTRADICTS",
        "RELATED",
    ]
    assert Verifier.V2_A2 == "V2+A2"


def test_enum_check_renders_in_list() -> None:
    ck = enum_check("status", JobStatus)
    assert "status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')" in str(ck.sqltext)
    assert ck.name == "status_enum"


def test_enum_check_nullable() -> None:
    ck = enum_check("status", JobStatus, nullable=True)
    assert str(ck.sqltext).startswith("status IS NULL OR status IN (")
