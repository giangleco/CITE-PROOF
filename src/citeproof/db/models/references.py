"""Bảng reference_entries, works, reference_checks."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import ParseMethod, ReferenceStatus, RefType, ResolvedBy
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class ReferenceEntry(IdCreatedMixin, Base):
    __tablename__ = "reference_entries"
    __table_args__ = (
        enum_check("ref_type", RefType),
        enum_check("parse_method", ParseMethod),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    ref_type: Mapped[str] = mapped_column(Text)
    parse_method: Mapped[str] = mapped_column(Text)
    doi_normalized: Mapped[str | None] = mapped_column(Text)


class Work(IdCreatedMixin, Base):
    __tablename__ = "works"
    __table_args__ = (
        UniqueConstraint("doi"),
        Index(
            "ix_works_title_norm_trgm",
            "title_norm",
            postgresql_using="gin",
            postgresql_ops={"title_norm": "gin_trgm_ops"},
        ),
    )

    doi: Mapped[str | None] = mapped_column(Text)
    openalex_id: Mapped[str | None] = mapped_column(Text)
    s2_id: Mapped[str | None] = mapped_column(Text)
    arxiv_id: Mapped[str | None] = mapped_column(Text)
    isbn: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    year: Mapped[int | None]
    venue: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    metadata_source: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReferenceCheck(IdCreatedMixin, Base):
    __tablename__ = "reference_checks"
    __table_args__ = (
        UniqueConstraint("run_id", "reference_entry_id"),
        enum_check("status", ReferenceStatus),
        enum_check("resolved_by", ResolvedBy),
        CheckConstraint(
            "status <> 'NOT_FOUND_IN_SEARCHED_SOURCES' OR cardinality(sources_searched) > 0",
            name="not_found_needs_sources",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE"), index=True
    )
    reference_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reference_entries.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(Text)
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("works.id", ondelete="SET NULL"), index=True
    )
    mismatches: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    sources_searched: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    sources_failed: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    resolved_by: Mapped[str] = mapped_column(Text, server_default=ResolvedBy.CASCADE.value)
    match_score: Mapped[Decimal | None] = mapped_column(Numeric)
    needs_user_review: Mapped[bool] = mapped_column(server_default=text("false"))
    url_alive: Mapped[bool | None]
    archive_url: Mapped[str | None] = mapped_column(Text)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
