"""Bảng feedback."""

import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import FeedbackTarget, FeedbackVerdict
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class Feedback(IdCreatedMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        enum_check("target_type", FeedbackTarget),
        enum_check("verdict", FeedbackVerdict),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID]
    verdict: Mapped[str] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
