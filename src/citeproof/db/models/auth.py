"""Bảng xác thực cho giao diện web: mã mời, phiên, lần đăng nhập (spec web UI §7.2).

Mã mời và token phiên chỉ lưu dưới dạng SHA-256; giá trị gốc không bao giờ vào DB.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.db.base import Base, IdCreatedMixin


class Invite(IdCreatedMixin, Base):
    __tablename__ = "invites"

    code_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebSession(IdCreatedMixin, Base):
    __tablename__ = "web_sessions"

    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(Text)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(IdCreatedMixin, Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_email_norm_created_at", "email_norm", "created_at"),
        Index("ix_login_attempts_ip_created_at", "ip", "created_at"),
    )

    email_norm: Mapped[str] = mapped_column(Text)
    ip: Mapped[str] = mapped_column(Text)
    succeeded: Mapped[bool]
