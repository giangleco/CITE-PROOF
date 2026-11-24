"""Bảng jobs (hàng đợi trên PostgreSQL, ADR-003)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import JobStatus, StageName
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class Job(IdCreatedMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("run_id", "stage"),
        enum_check("stage", StageName),
        enum_check("status", JobStatus),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_status_lease_expires_at", "status", "lease_expires_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=JobStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Token fencing của lần nhận job hiện tại; gia hạn lease không đổi giá trị này.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Lần nhận job gần nhất: bắt đầu và kết thúc (DONE hoặc FAILED, kể cả khi đặt lại lịch).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
