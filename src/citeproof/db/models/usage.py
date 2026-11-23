"""Sự kiện sử dụng để tính hạn mức (NFR-08); không mất khi người dùng xóa tài liệu."""

import uuid

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.db.base import Base, IdCreatedMixin


class UsageEvent(IdCreatedMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_user_id_created_at", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
