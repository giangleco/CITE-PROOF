"""Bảng citations, citation_links, claims."""

import uuid

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import LinkMethod
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class Citation(IdCreatedMixin, Base):
    __tablename__ = "citations"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    marker_text: Mapped[str] = mapped_column(Text)
    sentence_text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None]
    char_start: Mapped[int]
    char_end: Mapped[int]
    section: Mapped[str | None] = mapped_column(Text)


class CitationLink(IdCreatedMixin, Base):
    __tablename__ = "citation_links"
    __table_args__ = (enum_check("link_method", LinkMethod),)

    citation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("citations.id", ondelete="CASCADE"), index=True
    )
    reference_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reference_entries.id", ondelete="CASCADE"), index=True
    )
    link_method: Mapped[str] = mapped_column(Text)


class Claim(IdCreatedMixin, Base):
    __tablename__ = "claims"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    citation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("citations.id", ondelete="CASCADE"), index=True
    )
    claim_text: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(Text)
    # W2 trả `is_claim = false` → S8 cho `NOT_A_CLAIM` (spec QĐ-15).
    is_claim: Mapped[bool] = mapped_column(server_default=text("true"))
