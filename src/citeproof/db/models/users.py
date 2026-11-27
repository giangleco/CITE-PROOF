"""Bảng users."""

from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import UserRole
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class User(IdCreatedMixin, Base):
    __tablename__ = "users"
    __table_args__ = (enum_check("role", UserRole),)

    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default=UserRole.USER.value)
    consent_llm_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_llm_provider: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
