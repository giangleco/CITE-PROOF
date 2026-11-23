"""Bảng retrieval_results (kết quả truy xuất của S7)."""

import uuid

from sqlalchemy import REAL, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from citeproof.db.base import Base, IdCreatedMixin


class RetrievalResult(IdCreatedMixin, Base):
    __tablename__ = "retrieval_results"
    __table_args__ = (
        UniqueConstraint("run_id", "claim_id", "source_chunk_id", "query_variant"),
        Index("ix_retrieval_results_run_id_claim_id", "run_id", "claim_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE")
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int]
    score_lexical: Mapped[float | None] = mapped_column(REAL)
    score_vector: Mapped[float | None] = mapped_column(REAL)
    score_fused: Mapped[float | None] = mapped_column(REAL)
    query_variant: Mapped[str] = mapped_column(Text)
