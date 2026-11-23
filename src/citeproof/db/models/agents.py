"""Bảng agent_runs (quỹ đạo A1, A2; 07a §4.7.6)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import AgentName, AgentRunStatus, AgentTargetType
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class AgentRun(IdCreatedMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        enum_check("agent", AgentName),
        enum_check("target_type", AgentTargetType),
        enum_check("status", AgentRunStatus),
        Index("ix_agent_runs_run_id_agent", "run_id", "agent"),
        Index("ix_agent_runs_target_type_target_id", "target_type", "target_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE")
    )
    agent: Mapped[str] = mapped_column(Text)
    agent_version: Mapped[str] = mapped_column(Text)
    tool_catalog_version: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text)
    steps: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    n_steps: Mapped[int] = mapped_column(server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cost_estimate: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
