"""Bảng llm_calls, llm_cache, metadata_cache (07a §4.7)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import MetadataOutcome
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class LLMCall(IdCreatedMixin, Base):
    __tablename__ = "llm_calls"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="SET NULL"), index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    step_index: Mapped[int | None]
    stage: Mapped[str] = mapped_column(Text)
    task: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    latency_ms: Mapped[int]
    cost_estimate: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    cache_hit: Mapped[bool] = mapped_column(server_default=text("false"))
    cache_key: Mapped[str | None] = mapped_column(Text)
    cache_namespace: Mapped[str] = mapped_column(Text)


class LLMCacheEntry(Base):
    __tablename__ = "llm_cache"
    __table_args__ = (
        CheckConstraint(
            "namespace NOT LIKE 'doc:%' OR document_id IS NOT NULL",
            name="doc_namespace_needs_document",
        ),
        Index("ix_llm_cache_namespace", "namespace"),
    )

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    namespace: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    task: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hit_count: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetadataCacheEntry(Base):
    __tablename__ = "metadata_cache"
    __table_args__ = (enum_check("outcome", MetadataOutcome),)

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    query_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
