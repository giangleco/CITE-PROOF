"""Chép kết quả ngoài phạm vi từ lần chạy cha sang lần chạy con ở S9 (QĐ-30, T-505).

Lần chạy con chỉ tra lại hoặc đối chiếu lại một phần; để báo cáo của nó đầy đủ, S9 chép:

- `reference_checks` của lần chạy cha cho mọi tài liệu tham khảo chưa có dòng trong lần chạy
  con. Lần chạy `upload` bỏ qua S3 nên cả tài liệu trong phạm vi cũng lấy dòng của lần chạy
  cha; lần chạy `lookup_retry` giữ dòng vừa tra lại.
- `claim_verifications` của verifier cuối (`RunConfig.variant`) cho cặp (claim, reference)
  chưa có dòng của verifier đó trong lần chạy con, kèm `evidence` của chúng.

Mọi lệnh là `INSERT … SELECT … ON CONFLICT DO NOTHING` trên khóa duy nhất, nên chạy lại S9
(tiếp tục sau lỗi, mất lease) không thêm dòng. `evidence` không có khóa duy nhất: chỉ chép cho
dòng `claim_verifications` vừa được chèn ở lần gọi này (`RETURNING`, preflight F27).
`escalated_from` và `agent_run_id` của bản chép đặt NULL: chúng trỏ tới dòng của lần chạy cha.
"""

from uuid import UUID

from sqlalchemy import and_, literal, null, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from citeproof.db.models import ClaimVerification, Evidence, ReferenceCheck
from citeproof.pipeline.config import RunInfo

__all__ = ["merge_parent_results"]

_REFERENCE_COLUMNS = (
    "reference_entry_id",
    "status",
    "work_id",
    "mismatches",
    "sources_searched",
    "sources_failed",
    "resolved_by",
    "match_score",
    "needs_user_review",
    "url_alive",
    "archive_url",
)
_VERIFICATION_COLUMNS = (
    "claim_id",
    "reference_entry_id",
    "status",
    "unverifiable_reason",
    "content_level_used",
    "explanation",
    "limitations",
    "needs_user_review",
    "downgraded_by_rule",
    "model",
    "prompt_version",
    "schema_version",
    "verifier",
)
_EVIDENCE_COLUMNS = ("source_chunk_id", "relation", "quote_text", "char_start", "char_end")


async def _merge_reference_checks(session: AsyncSession, run_id: UUID, parent_id: UUID) -> None:
    source = select(
        literal(run_id).label("run_id"),
        *(getattr(ReferenceCheck, name) for name in _REFERENCE_COLUMNS),
        null().label("agent_run_id"),
    ).where(ReferenceCheck.run_id == parent_id)
    await session.execute(
        insert(ReferenceCheck)
        .from_select(["run_id", *_REFERENCE_COLUMNS, "agent_run_id"], source)
        .on_conflict_do_nothing(index_elements=["run_id", "reference_entry_id"])
    )


async def _merge_claim_verifications(
    session: AsyncSession, run_id: UUID, parent_id: UUID, verifier: str
) -> list[UUID]:
    source = select(
        literal(run_id).label("run_id"),
        *(getattr(ClaimVerification, name) for name in _VERIFICATION_COLUMNS),
        null().label("escalated_from"),
        null().label("agent_run_id"),
    ).where(ClaimVerification.run_id == parent_id, ClaimVerification.verifier == verifier)
    inserted = await session.execute(
        insert(ClaimVerification)
        .from_select(["run_id", *_VERIFICATION_COLUMNS, "escalated_from", "agent_run_id"], source)
        .on_conflict_do_nothing(
            index_elements=["run_id", "claim_id", "reference_entry_id", "verifier"]
        )
        .returning(ClaimVerification.id)
    )
    return list(inserted.scalars())


async def _copy_evidence(session: AsyncSession, parent_id: UUID, copied: list[UUID]) -> None:
    child = aliased(ClaimVerification)
    parent = aliased(ClaimVerification)
    source = (
        select(
            child.id,
            *(getattr(Evidence, name) for name in _EVIDENCE_COLUMNS),
            # Giữ thời điểm gốc: báo cáo xếp bằng chứng theo `created_at`.
            Evidence.created_at,
        )
        .select_from(child)
        .join(
            parent,
            and_(
                parent.run_id == parent_id,
                parent.claim_id == child.claim_id,
                parent.reference_entry_id.is_not_distinct_from(child.reference_entry_id),
                parent.verifier == child.verifier,
            ),
        )
        .join(Evidence, Evidence.claim_verification_id == parent.id)
        .where(child.id.in_(copied))
    )
    await session.execute(
        insert(Evidence).from_select(
            ["claim_verification_id", *_EVIDENCE_COLUMNS, "created_at"], source
        )
    )


async def merge_parent_results(session: AsyncSession, info: RunInfo) -> None:
    """Chép kết quả của lần chạy cha mà lần chạy con chưa có; không làm gì nếu không có cha."""
    parent_id = info.parent_run_id
    if parent_id is None:
        return
    await _merge_reference_checks(session, info.run_id, parent_id)
    copied = await _merge_claim_verifications(
        session, info.run_id, parent_id, info.config.variant.value
    )
    if copied:
        await _copy_evidence(session, parent_id, copied)
