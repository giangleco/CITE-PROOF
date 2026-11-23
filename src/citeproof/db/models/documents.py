"""Bảng documents."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.core.enums import DocumentKind, DomainFlag, Language
from citeproof.db.base import Base, IdCreatedMixin, enum_check


class Document(IdCreatedMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        enum_check("kind", DocumentKind),
        enum_check("language", Language),
        enum_check("domain_flag", DomainFlag),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    parsed_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, server_default=Language.UNKNOWN.value)
    domain_flag: Mapped[str] = mapped_column(Text, server_default=DomainFlag.UNKNOWN.value)
    page_count: Mapped[int | None]
    parse_warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
