"""RRF (spec §7.7, QĐ-24)."""

import uuid

import pytest

from citeproof.modules.retrieval.fusion import rrf
from citeproof.modules.retrieval.lexical import LexicalHit


def hit(index: int, score: float = 1.0) -> LexicalHit:
    return LexicalHit(uuid.UUID(int=index + 1), index, score)


def test_rrf_scores_sum_over_variants() -> None:
    fused = rrf({"original": [hit(0), hit(1)], "translated": [hit(1), hit(2)]}, limit=10)
    assert [c.chunk_index for c in fused] == [1, 0, 2]
    assert fused[0].score_fused == pytest.approx(1 / 62 + 1 / 61)
    assert [c.rank for c in fused] == [1, 2, 3]
    assert all(c.score_vector is None for c in fused)


def test_rrf_limit_and_lexical_score() -> None:
    fused = rrf({"a": [hit(0, 0.2)], "b": [hit(0, 0.7)]}, limit=1)
    assert len(fused) == 1 and fused[0].score_lexical == 0.7
    assert rrf({}, limit=3) == []


def test_rrf_tie_break_deterministic() -> None:
    variants = {"a": [hit(5)], "b": [hit(2)], "c": [hit(9)]}
    first = rrf(variants, limit=10)
    assert [c.chunk_index for c in first] == [2, 5, 9]
    assert rrf(variants, limit=10) == first
    assert rrf(dict(reversed(list(variants.items()))), limit=10) == first
