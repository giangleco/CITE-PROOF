"""Bảng source_files, source_chunks."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import ChunkRegion, ContentLevel, SourceOrigin, TitleMatch
from citeproof.db.base import Base, IdCreatedMixin, enum_check

EMBEDDING_DIM = 1024


class SourceFile(IdCreatedMixin, Base):
    __tablename__ = "source_files"
    __table_args__ = (
        enum_check("origin", SourceOrigin),
        enum_check("content_level", ContentLevel),
        enum_check("title_match", TitleMatch),
        CheckConstraint(
            "origin <> 'USER_UPLOAD' OR (owner_user_id IS NOT NULL "
            "AND reference_entry_id IS NOT NULL)",
            name="user_upload_needs_owner_and_reference",
        ),
        CheckConstraint(
            "origin <> 'OPEN_ACCESS' OR (owner_user_id IS NULL AND reference_entry_id IS NULL)",
            name="open_access_is_shared",
        ),
    )

    work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("works.id", ondelete="SET NULL"), index=True
    )
    reference_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reference_entries.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(Text)
    access_status: Mapped[str] = mapped_column(Text)
    content_level: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    title_match: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceChunk(IdCreatedMixin, Base):
    __tablename__ = "source_chunks"
    __table_args__ = (
        enum_check("region", ChunkRegion),
        Index("ix_source_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None]
    section: Mapped[str | None] = mapped_column(Text)
    char_start: Mapped[int]
    char_end: Mapped[int]
    region: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True)
    )
