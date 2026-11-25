"""Chỉ số đánh giá thuần (spec §11; 13 §B.1)."""

import pytest

from citeproof.evaluation.metrics import (
    bootstrap_ci,
    confusion,
    macro_f1,
    mrr,
    per_class_f1,
    percentile,
    recall_at_k,
)

LABELS = ["S", "U"]


def test_macro_f1_known_values() -> None:
    gold, pred = ["S", "S", "U"], ["S", "U", "U"]
    per_class = per_class_f1(gold, pred, LABELS)
    assert per_class["S"] == pytest.approx(2 / 3, abs=1e-4)
    assert per_class["U"] == pytest.approx(2 / 3, abs=1e-4)
    assert macro_f1(gold, pred, LABELS) == pytest.approx(0.6667, abs=1e-4)


def test_f1_of_class_never_seen_is_zero() -> None:
    assert per_class_f1(["S"], ["S"], ["S", "U"]) == {"S": 1.0, "U": 0.0}


def test_confusion_matrix() -> None:
    gold = ["S", "S", "U", "C"]
    pred = ["S", "U", "U", "S"]
    # Hàng: nhãn vàng; cột: dự đoán, theo thứ tự `labels`.
    assert confusion(gold, pred, ["S", "U", "C"]) == [[1, 1, 0], [0, 1, 0], [1, 0, 0]]


def test_confusion_rejects_unknown_label() -> None:
    with pytest.raises(ValueError):
        confusion(["S"], ["X"], ["S"])


def test_lengths_must_match() -> None:
    with pytest.raises(ValueError):
        macro_f1(["S"], ["S", "U"], LABELS)


def test_recall_at_k_and_mrr() -> None:
    ranked = [["a", "b"], ["c", "d"]]
    relevant = [["b"], ["x"]]
    assert recall_at_k(ranked, relevant, 1) == 0.0
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert mrr(ranked, relevant) == 0.25


def test_retrieval_metrics_skip_queries_without_relevant_chunks() -> None:
    assert recall_at_k([["a"], ["b"]], [["a"], []], 1) == 1.0
    assert mrr([["a"], ["b"]], [["a"], []]) == 1.0
    assert recall_at_k([["a"]], [[]], 1) == 0.0


def test_bootstrap_deterministic() -> None:
    gold = ["S", "S", "U", "U", "S", "U", "S", "S", "U", "S"]
    pred = ["S", "U", "U", "U", "S", "S", "S", "S", "U", "S"]

    def metric(g: list[str] | tuple[str, ...], p: list[str] | tuple[str, ...]) -> float:
        return macro_f1(g, p, LABELS)

    first = bootstrap_ci(gold, pred, metric)
    second = bootstrap_ci(gold, pred, metric)
    assert first == second
    lower, upper = first
    assert lower <= metric(gold, pred) <= upper
    assert bootstrap_ci(gold, pred, metric, seed=1) != first or lower == upper


def test_percentile_linear() -> None:
    assert percentile([10.0, 20.0, 30.0, 40.0], 0.5) == 25.0
    assert percentile([5.0], 0.95) == 5.0
    with pytest.raises(ValueError):
        percentile([], 0.5)
