import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import FeedbackVerdict
from citeproof.core.errors import InputError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import ClaimVerification, Feedback
from citeproof.services import feedback
from citeproof.services.sample import seed_sample
from tests.services.helpers import new_user

SessionFactory = async_sessionmaker[AsyncSession]


async def _cv(s: AsyncSession) -> object:
    return (await s.execute(select(ClaimVerification.id))).scalars().first()


@pytest.mark.db
async def test_feedback_upsert(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        cv = await _cv(s)
        await feedback.save_feedback(
            s, user=user, claim_verification_id=cv, verdict=FeedbackVerdict.AGREE, comment=None
        )  # type: ignore[arg-type]
        await feedback.save_feedback(
            s,
            user=user,
            claim_verification_id=cv,
            verdict=FeedbackVerdict.DISAGREE,
            comment="  sai  ",
        )  # type: ignore[arg-type]
        await s.commit()
        rows = (await s.execute(select(Feedback))).scalars().all()
        mine = await feedback.my_feedback(s, user=user, claim_verification_ids=[cv])  # type: ignore[list-item]
    assert len(rows) == 1 and rows[0].verdict == "DISAGREE" and rows[0].comment == "sai"
    assert rows[0].target_type == "CLAIM_VERIFICATION"
    assert mine == {cv: FeedbackVerdict.DISAGREE}


@pytest.mark.db
async def test_feedback_foreign_target_404(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        owner = await new_user(s)
        other = await new_user(s, "o@b.vn")
        await seed_sample(s, settings, user_id=owner.id)
        await s.commit()
        cv = await _cv(s)
        with pytest.raises(NotFoundError):
            await feedback.save_feedback(
                s, user=other, claim_verification_id=cv, verdict=FeedbackVerdict.AGREE, comment=None
            )  # type: ignore[arg-type]


@pytest.mark.db
async def test_comment_too_long(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    async with session_factory() as s:
        user = await new_user(s)
        await seed_sample(s, settings, user_id=user.id)
        await s.commit()
        cv = await _cv(s)
        with pytest.raises(InputError) as exc:
            await feedback.save_feedback(
                s,
                user=user,
                claim_verification_id=cv,
                verdict=FeedbackVerdict.UNSURE,
                comment="x" * 2001,
            )  # type: ignore[arg-type]
    assert exc.value.code == "COMMENT_TOO_LONG"
