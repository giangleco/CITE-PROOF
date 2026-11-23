"""Phản hồi của người dùng cho từng kết quả nhận định (FR-70, spec web UI §7.6).

Mỗi người dùng có tối đa một phản hồi cho mỗi kết quả; gửi lại thì cập nhật.
"""

import unicodedata
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import FeedbackTarget, FeedbackVerdict
from citeproof.core.errors import InputError, NotFoundError
from citeproof.db.models import ClaimVerification, Feedback, User, VerificationRun

MAX_COMMENT = 2000


async def owning_run_id(session: AsyncSession, *, user: User, claim_verification_id: UUID) -> UUID:
    run_id = await session.scalar(
        select(ClaimVerification.run_id)
        .join(VerificationRun, VerificationRun.id == ClaimVerification.run_id)
        .where(ClaimVerification.id == claim_verification_id, VerificationRun.user_id == user.id)
    )
    if run_id is None:
        raise NotFoundError()
    return run_id


async def save_feedback(
    session: AsyncSession,
    *,
    user: User,
    claim_verification_id: UUID,
    verdict: FeedbackVerdict,
    comment: str | None,
) -> None:
    await owning_run_id(session, user=user, claim_verification_id=claim_verification_id)
    text = unicodedata.normalize("NFC", comment or "").strip() or None
    if text is not None and len(text) > MAX_COMMENT:
        raise InputError(f"Ghi chú tối đa {MAX_COMMENT} ký tự.", code="COMMENT_TOO_LONG")
    existing = (
        await session.execute(
            select(Feedback)
            .where(
                Feedback.user_id == user.id,
                Feedback.target_type == FeedbackTarget.CLAIM_VERIFICATION.value,
                Feedback.target_id == claim_verification_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Feedback(
                user_id=user.id,
                target_type=FeedbackTarget.CLAIM_VERIFICATION.value,
                target_id=claim_verification_id,
                verdict=verdict.value,
                comment=text,
            )
        )
    else:
        existing.verdict = verdict.value
        existing.comment = text
    await session.flush()


async def my_feedback(
    session: AsyncSession, *, user: User, claim_verification_ids: Sequence[UUID]
) -> dict[UUID, FeedbackVerdict]:
    if not claim_verification_ids:
        return {}
    rows = await session.execute(
        select(Feedback.target_id, Feedback.verdict).where(
            Feedback.user_id == user.id,
            Feedback.target_type == FeedbackTarget.CLAIM_VERIFICATION.value,
            Feedback.target_id.in_(claim_verification_ids),
        )
    )
    return {target: FeedbackVerdict(verdict) for target, verdict in rows}
