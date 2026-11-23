"""Bảng verification_runs, claim_verifications, evidence."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    RunStatus,
    StageName,
    UnverifiableReason,
    Verifier,
)
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class VerificationRun(IdCreatedMixin, Base):
    __tablename__ = "verification_runs"
    __table_args__ = (
        enum_check("status", RunStatus),
        enum_check("current_stage", StageName, nullable=True),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(Text, server_default=RunStatus.QUEUED.value)
    current_stage: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_estimate: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="SET NULL"), index=True
    )


class ClaimVerification(IdCreatedMixin, Base):
    __tablename__ = "claim_verifications"
    __table_args__ = (
        UniqueConstraint("run_id", "claim_id", "verifier"),
        enum_check("status", ClaimStatus),
        enum_check("unverifiable_reason", UnverifiableReason, nullable=True),
        enum_check("content_level_used", ContentLevelUsed),
        enum_check("verifier", Verifier),
        CheckConstraint(
            "status <> 'UNVERIFIABLE' OR unverifiable_reason IS NOT NULL",
            name="unverifiable_needs_reason",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE"), index=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), index=True
    )
    reference_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reference_entries.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(Text)
    unverifiable_reason: Mapped[str | None] = mapped_column(Text)
    content_level_used: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)
    needs_user_review: Mapped[bool] = mapped_column(server_default=text("false"))
    downgraded_by_rule: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    verifier: Mapped[str] = mapped_column(Text)
    escalated_from: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claim_verifications.id", ondelete="SET NULL"), index=True
    )


class Evidence(IdCreatedMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (enum_check("relation", EvidenceRelation),)

    claim_verification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claim_verifications.id", ondelete="CASCADE"), index=True
    )
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(Text)
    quote_text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int]
    char_end: Mapped[int]
