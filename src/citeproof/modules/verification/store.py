"""Đọc và ghi của S8: chi phí lần chạy, trạng thái và metadata reference, `claim_verifications`.

`write_outcomes` gọi trong `fenced_session` của stage; mọi hàm khác chỉ đọc.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import ReferenceStatus
from citeproof.core.errors import InternalError
from citeproof.db.models import (
    ClaimVerification,
    Evidence,
    LLMCall,
    ReferenceCheck,
    ReferenceEntry,
    Work,
)
from citeproof.modules.retrieval.pairs import ClaimPair
from citeproof.modules.sources.selection import entry_works
from citeproof.modules.verification.context import RefMeta
from citeproof.modules.verification.verifiers.base import VerificationOutcome
from citeproof.pipeline.config import RunInfo

__all__ = [
    "done_keys",
    "pair_key",
    "reference_meta",
    "reference_statuses",
    "run_cost",
    "write_outcomes",
]


async def run_cost(session: AsyncSession, run_id: UUID) -> Decimal:
    """Tổng `llm_calls.cost_estimate` của lần chạy (QĐ-32)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(LLMCall.cost_estimate), 0)).where(LLMCall.run_id == run_id)
    )
    return Decimal(total or 0)


def pair_key(claim_id: UUID, reference_entry_id: UUID | None) -> str:
    return f"{claim_id}:{reference_entry_id}"


async def done_keys(session: AsyncSession, run_id: UUID, verifier: str) -> set[str]:
    """Cặp đã có dòng `claim_verifications` cho verifier của lần chạy (tiếp tục sau lỗi)."""
    rows = await session.execute(
        select(ClaimVerification.claim_id, ClaimVerification.reference_entry_id).where(
            ClaimVerification.run_id == run_id, ClaimVerification.verifier == verifier
        )
    )
    return {pair_key(claim_id, ref_id) for claim_id, ref_id in rows}


async def reference_statuses(session: AsyncSession, info: RunInfo) -> dict[UUID, ReferenceStatus]:
    """`reference_checks.status` theo reference; lần chạy con dùng dòng của lần chạy cha khi
    không có dòng riêng (dòng của lần chạy này luôn thắng)."""
    run_ids = [info.run_id] if info.parent_run_id is None else [info.run_id, info.parent_run_id]
    rows = await session.execute(
        select(ReferenceCheck.reference_entry_id, ReferenceCheck.status, ReferenceCheck.run_id)
        .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
        .where(ReferenceCheck.run_id.in_(run_ids), ReferenceEntry.document_id == info.document_id)
    )
    chosen: dict[UUID, tuple[UUID, str]] = {}
    for entry_id, status, run_id in rows:
        if entry_id not in chosen or run_id == info.run_id:
            chosen[entry_id] = (run_id, status)
    return {entry_id: ReferenceStatus(status) for entry_id, (_, status) in chosen.items()}


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _authors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = _text(item.get("name")) or _text(item.get("display_name"))
            if name:
                names.append(name)
    return tuple(names)


async def reference_meta(session: AsyncSession, info: RunInfo) -> dict[UUID, RefMeta]:
    """Metadata cho W5: trường đã parse của reference, trường thiếu lấy từ work đã khớp."""
    entries = (
        await session.execute(
            select(ReferenceEntry.id, ReferenceEntry.parsed).where(
                ReferenceEntry.document_id == info.document_id
            )
        )
    ).all()
    work_of = dict(await entry_works(session, info, scoped=False))
    works: dict[UUID, Work] = {}
    if work_of:
        works = {
            work.id: work
            for work in (
                await session.execute(select(Work).where(Work.id.in_(set(work_of.values()))))
            ).scalars()
        }
    result: dict[UUID, RefMeta] = {}
    for entry_id, parsed in entries:
        data = parsed if isinstance(parsed, dict) else {}
        work = works.get(work_of[entry_id]) if entry_id in work_of else None
        authors = _authors(data.get("authors"))
        result[entry_id] = RefMeta(
            title=_text(data.get("title")) or (work.title if work else None),
            authors=authors or (_authors(work.authors) if work else ()),
            year=_year(data.get("year")) or (work.year if work else None),
            venue=_text(data.get("venue")) or (work.venue if work else None),
        )
    return result


async def write_outcomes(
    session: AsyncSession,
    *,
    info: RunInfo,
    pair: ClaimPair,
    outcomes: Sequence[VerificationOutcome],
) -> list[UUID]:
    """Ghi `claim_verifications` và `evidence` theo thứ tự; trả id các dòng.

    `ON CONFLICT DO NOTHING` trên khóa (run, claim, reference, verifier): dòng đã có được dùng
    lại (không ghi lại bằng chứng), nên cặp `V2+A2` tiếp tục sau lỗi không ghi trùng dòng `V2`.
    `escalated_from` của kết quả `escalates_previous` là id dòng trước nó.
    """
    ids: list[UUID] = []
    previous: UUID | None = None
    for item in outcomes:
        outcome = item.outcome
        inserted = await session.scalar(
            insert(ClaimVerification)
            .values(
                run_id=info.run_id,
                claim_id=pair.claim_id,
                reference_entry_id=pair.reference_entry_id,
                status=outcome.status.value,
                unverifiable_reason=(
                    outcome.unverifiable_reason.value if outcome.unverifiable_reason else None
                ),
                content_level_used=outcome.content_level_used.value,
                explanation=outcome.explanation,
                limitations=outcome.limitations,
                needs_user_review=outcome.needs_user_review,
                downgraded_by_rule=outcome.downgraded_by_rule,
                model=item.model,
                prompt_version=item.prompt_version,
                schema_version=item.schema_version,
                verifier=item.verifier.value,
                escalated_from=previous if item.escalates_previous else None,
                agent_run_id=item.agent_run_id,
            )
            .on_conflict_do_nothing()
            .returning(ClaimVerification.id)
        )
        if inserted is None:
            existing = await session.scalar(
                select(ClaimVerification.id).where(
                    ClaimVerification.run_id == info.run_id,
                    ClaimVerification.claim_id == pair.claim_id,
                    ClaimVerification.reference_entry_id.is_not_distinct_from(
                        pair.reference_entry_id
                    ),
                    ClaimVerification.verifier == item.verifier.value,
                )
            )
            if existing is None:  # xung đột ràng buộc khác: không bao giờ xảy ra
                raise InternalError("claim_verifications: không ghi được và không tìm thấy dòng")
            row_id = existing
        else:
            row_id = inserted
            for evidence in outcome.evidence:
                # clock_timestamp(): bằng chứng giữ đúng thứ tự khi đọc theo `created_at`.
                await session.execute(
                    insert(Evidence).values(
                        claim_verification_id=row_id,
                        source_chunk_id=evidence.source_chunk_id,
                        relation=evidence.relation.value,
                        quote_text=evidence.quote_text,
                        char_start=evidence.char_start,
                        char_end=evidence.char_end,
                        created_at=func.clock_timestamp(),
                    )
                )
        ids.append(row_id)
        previous = row_id
    return ids
