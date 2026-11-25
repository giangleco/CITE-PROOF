"""Quy tắc gộp tất định của V3 (`07a` §4.4.3, spec §7.8).

| Trạng thái các ý con | Kết quả |
| có ít nhất một `CONTRADICTED` | `CONTRADICTED` |
| tất cả `SUPPORTED` / `UNSUPPORTED` | giữ nguyên |
| tất cả `UNVERIFIABLE` | `UNVERIFIABLE`, lý do của ý đầu tiên |
| còn lại | `PARTIALLY_SUPPORTED`, gắn cờ "nên tự kiểm tra" |

Bằng chứng là hợp các bằng chứng hợp lệ theo thứ tự ý con (bỏ trùng chunk + vị trí). Mức nội
dung là mức cao nhất đã dùng. Một ý duy nhất trả nguyên kết quả của ý đó.
"""

from collections.abc import Callable, Sequence

from citeproof.core.enums import ClaimStatus, ContentLevelUsed, UnverifiableReason
from citeproof.core.errors import InternalError
from citeproof.modules.verification.explanations import (
    ExplanationLanguage,
    deterministic_explanation,
    status_explanation,
)
from citeproof.modules.verification.rules import Outcome, ValidatedEvidence

__all__ = ["aggregate"]

_LEVEL_ORDER = {
    ContentLevelUsed.NONE: 0,
    ContentLevelUsed.ABSTRACT: 1,
    ContentLevelUsed.FULL_TEXT: 2,
}


def _status(statuses: Sequence[ClaimStatus]) -> tuple[ClaimStatus, bool]:
    """`(trạng thái gộp, trường hợp lẫn)`."""
    if ClaimStatus.CONTRADICTED in statuses:
        return ClaimStatus.CONTRADICTED, False
    first = statuses[0]
    if all(s is first for s in statuses) and first in {
        ClaimStatus.SUPPORTED,
        ClaimStatus.UNSUPPORTED,
        ClaimStatus.UNVERIFIABLE,
    }:
        return first, False
    return ClaimStatus.PARTIALLY_SUPPORTED, True


def _join(outcomes: Sequence[Outcome], text: Callable[[Outcome], str | None]) -> str | None:
    parts = [
        f"s{i}: {value}" for i, o in enumerate(outcomes, start=1) if (value := text(o)) is not None
    ]
    return "; ".join(parts) if parts else None


def _fallback_explanation(
    status: ClaimStatus, reason: UnverifiableReason | None, language: ExplanationLanguage
) -> str:
    if status is ClaimStatus.UNVERIFIABLE and reason is not None:
        return deterministic_explanation(reason, language)
    return status_explanation(status, language)


def aggregate(outcomes: Sequence[Outcome], *, language: ExplanationLanguage) -> Outcome:
    if not outcomes:
        raise InternalError("Gộp V3 không có kết quả ý con")
    if len(outcomes) == 1:
        return outcomes[0]
    status, mixed = _status([o.status for o in outcomes])
    reason = outcomes[0].unverifiable_reason if status is ClaimStatus.UNVERIFIABLE else None
    seen: set[tuple[object, int, int]] = set()
    evidence: list[ValidatedEvidence] = []
    for outcome in outcomes:
        for item in outcome.evidence:
            key = (item.source_chunk_id, item.char_start, item.char_end)
            if key not in seen:
                seen.add(key)
                evidence.append(item)
    explanation = _join(outcomes, lambda o: o.explanation.strip() or None)
    return Outcome(
        status=status,
        unverifiable_reason=reason,
        evidence=tuple(evidence),
        content_level_used=max(
            (o.content_level_used for o in outcomes), key=_LEVEL_ORDER.__getitem__
        ),
        needs_user_review=mixed or any(o.needs_user_review for o in outcomes),
        downgraded_by_rule=next(
            (o.downgraded_by_rule for o in outcomes if o.downgraded_by_rule), None
        ),
        explanation=explanation or _fallback_explanation(status, reason, language),
        limitations=_join(outcomes, lambda o: o.limitations),
    )
