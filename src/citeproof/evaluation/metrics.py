"""Chỉ số đánh giá thuần, không phụ thuộc DB (spec §11; 13 §B.1).

Phân loại: ma trận nhầm lẫn, F1 từng lớp, Macro-F1 trên đúng danh sách nhãn truyền vào (lớp
không xuất hiện ở cả nhãn vàng lẫn dự đoán có F1 = 0), khoảng tin cậy bootstrap theo phân vị.
Truy xuất: Recall@k (tỷ lệ chunk liên quan nằm trong top-k, trung bình theo truy vấn) và MRR;
truy vấn không có chunk liên quan nào bị bỏ qua.
"""

import math
import random
from collections.abc import Callable, Sequence

__all__ = [
    "bootstrap_ci",
    "confusion",
    "macro_f1",
    "mrr",
    "per_class_f1",
    "percentile",
    "recall_at_k",
]

Metric = Callable[[Sequence[str], Sequence[str]], float]


def _check_lengths(gold: Sequence[object], pred: Sequence[object]) -> None:
    if len(gold) != len(pred):
        raise ValueError(f"độ dài khác nhau: {len(gold)} nhãn vàng, {len(pred)} dự đoán")


def confusion(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> list[list[int]]:
    """Ma trận `labels x labels`: hàng là nhãn vàng, cột là dự đoán."""
    _check_lengths(gold, pred)
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for g, p in zip(gold, pred, strict=True):
        if g not in index or p not in index:
            raise ValueError("nhãn nằm ngoài danh sách labels")
        matrix[index[g]][index[p]] += 1
    return matrix


def per_class_f1(
    gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]
) -> dict[str, float]:
    matrix = confusion(gold, pred, labels)
    result: dict[str, float] = {}
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        fp = sum(matrix[r][i] for r in range(len(labels))) - tp
        fn = sum(matrix[i]) - tp
        denominator = 2 * tp + fp + fn
        result[label] = 2 * tp / denominator if denominator else 0.0
    return result


def macro_f1(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> float:
    scores = per_class_f1(gold, pred, labels)
    return sum(scores.values()) / len(scores) if scores else 0.0


def _with_relevant(
    ranked: Sequence[Sequence[str]], relevant: Sequence[Sequence[str]]
) -> list[tuple[Sequence[str], set[str]]]:
    _check_lengths(ranked, relevant)
    return [(r, set(rel)) for r, rel in zip(ranked, relevant, strict=True) if rel]


def recall_at_k(
    ranked: Sequence[Sequence[str]], relevant: Sequence[Sequence[str]], k: int
) -> float:
    queries = _with_relevant(ranked, relevant)
    if not queries:
        return 0.0
    return sum(len(rel & set(r[:k])) / len(rel) for r, rel in queries) / len(queries)


def mrr(ranked: Sequence[Sequence[str]], relevant: Sequence[Sequence[str]]) -> float:
    queries = _with_relevant(ranked, relevant)
    if not queries:
        return 0.0
    total = 0.0
    for r, rel in queries:
        total += next((1.0 / rank for rank, item in enumerate(r, start=1) if item in rel), 0.0)
    return total / len(queries)


def percentile(values: Sequence[float], q: float) -> float:
    """Phân vị `q` ∈ [0, 1], nội suy tuyến tính giữa hai giá trị kề nhau."""
    if not values:
        raise ValueError("không có giá trị nào")
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_ci(
    gold: Sequence[str],
    pred: Sequence[str],
    metric: Metric,
    *,
    n: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Khoảng tin cậy `1 - alpha` của `metric` bằng bootstrap phân vị (lấy mẫu lại có hoàn lại)."""
    _check_lengths(gold, pred)
    if not gold:
        return (0.0, 0.0)
    rng = random.Random(seed)
    size = len(gold)
    scores = []
    for _ in range(n):
        picks = [rng.randrange(size) for _ in range(size)]
        scores.append(metric([gold[i] for i in picks], [pred[i] for i in picks]))
    return (percentile(scores, alpha / 2), percentile(scores, 1 - alpha / 2))
