"""Gộp kết quả của các biến thể truy vấn bằng Reciprocal Rank Fusion (spec §7.7, QĐ-24)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from citeproof.modules.retrieval.lexical import LexicalHit

__all__ = ["ScoredChunk", "rrf"]


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: UUID
    chunk_index: int
    # Điểm lexical cao nhất của chunk qua các biến thể; v1 không có vector (ADR-007).
    score_lexical: float | None
    score_vector: float | None
    score_fused: float
    rank: int


def rrf(
    variants: Mapping[str, Sequence[LexicalHit]], *, limit: int, k: int = 60
) -> list[ScoredChunk]:
    """Điểm = Σ 1/(k + hạng) trên mỗi biến thể (hạng từ 1); đồng điểm theo `chunk_index`."""
    fused: dict[UUID, float] = {}
    best: dict[UUID, LexicalHit] = {}
    for name in sorted(variants):  # thứ tự cố định: tổng số thực không phụ thuộc thứ tự dict
        for rank, hit in enumerate(variants[name], start=1):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            known = best.get(hit.chunk_id)
            if known is None or hit.score > known.score:
                best[hit.chunk_id] = hit
    ordered = sorted(fused, key=lambda cid: (-fused[cid], best[cid].chunk_index))
    return [
        ScoredChunk(
            chunk_id=cid,
            chunk_index=best[cid].chunk_index,
            score_lexical=best[cid].score,
            score_vector=None,
            score_fused=fused[cid],
            rank=position,
        )
        for position, cid in enumerate(ordered[: max(limit, 0)], start=1)
    ]
