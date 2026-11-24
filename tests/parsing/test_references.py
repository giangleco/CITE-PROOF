"""Bộ tách danh mục dùng chung S1/S2: đánh số lại sau tiêu đề nhóm, dòng chỉ có nhãn."""

import pytest

from citeproof.parsing.references import (
    is_group_heading,
    join_lines,
    split_reference_lines,
)


def _lines(*texts: str) -> list[tuple[int | None, str]]:
    return [(None, text) for text in texts]


def _pairs(*texts: str, group_breaks: frozenset[int] = frozenset()) -> list[tuple[str | None, str]]:
    entries = split_reference_lines(_lines(*texts), group_breaks=group_breaks)
    return [(ref.label, ref.raw) for ref in entries]


def test_restart_after_unrecognised_subheading() -> None:
    # Phép thử của reviewer: đánh số lại sau một tiêu đề nhóm.
    assert _pairs(
        "[1] Nguyễn A (2020). X.",
        "[2] Lê B (2019). Y.",
        "",
        "Tài liệu tiếng nước ngoài",
        "",
        "[1] Smith, C. (2017). Z.",
        "[2] Doe, D. (2018). W.",
    ) == [
        ("1", "Nguyễn A (2020). X."),
        ("2", "Lê B (2019). Y."),
        ("1", "Smith, C. (2017). Z."),
        ("2", "Doe, D. (2018). W."),
    ]


@pytest.mark.parametrize(
    "heading",
    [
        "Tài liệu tiếng nước ngoài",
        "Tài liệu tham khảo tiếng Anh",
        "Phần tiếng Việt",
        "TIẾNG ANH",
        "A. Tài liệu tiếng Việt",
        "II. Tài liệu nước ngoài",
        "Sách tham khảo tiếng Việt",
        "English references",
    ],
)
def test_group_heading_forms(heading: str) -> None:
    assert is_group_heading(heading)
    assert _pairs("[1] A (2020). X.", heading, "[1] B (2021). Y.") == [
        ("1", "A (2020). X."),
        ("1", "B (2021). Y."),
    ]


@pytest.mark.parametrize(
    "line", ["tiếng Việt", "Tài liệu tham khảo", "Tiếng Việt hiện đại, NXB Giáo dục."]
)
def test_not_group_headings(line: str) -> None:
    assert not is_group_heading(line)


def test_bracketed_label_after_boundary_restarts_numbering() -> None:
    assert _pairs("[1] A (2020). X.", "[2] B (2021). Y.", "", "[1] C (2017). Z.") == [
        ("1", "A (2020). X."),
        ("2", "B (2021). Y."),
        ("1", "C (2017). Z."),
    ]
    # Không có ranh giới: "[1]" giữa dòng vẫn là dòng bị ngắt của mục trước.
    assert _pairs("[2] B (2021). Y. Xem", "[1] ở trên.") == [("2", "B (2021). Y. Xem [1] ở trên.")]
    # Nhãn "n." nhỏ hơn sau ranh giới vẫn là dòng bị ngắt ("2. pp. 1-9" sau "[3]").
    assert _pairs("[3] C. Author, vol. 12, no.", "", "2. pp. 1-9, 2021.") == [
        ("3", "C. Author, vol. 12, no. 2. pp. 1-9, 2021.")
    ]


def test_heading_like_line_before_restart_is_not_appended() -> None:
    assert _pairs(
        "1. Nguyễn A (2020). X.",
        "2. Lê B (2019). Y.",
        "",
        "Sách chuyên khảo",
        "",
        "1. Trần C (2018). Z.",
    ) == [
        ("1", "Nguyễn A (2020). X."),
        ("2", "Lê B (2019). Y."),
        ("1", "Trần C (2018). Z."),
    ]
    # Không đánh số lại sau dòng đó: là dòng tiếp của mục trước, giữ nguyên văn.
    assert _pairs("[1] Nguyễn A (2020). X,", "", "Tạp chí Mẫu", "", "[2] Lê B (2019). Y.") == [
        ("1", "Nguyễn A (2020). X, Tạp chí Mẫu"),
        ("2", "Lê B (2019). Y."),
    ]


def test_explicit_group_breaks_reset_numbering() -> None:
    # Đoạn style Heading trong khối (S2 truyền chỉ số dòng): bỏ dòng, đặt lại thứ tự nhãn.
    assert _pairs(
        "[1] A (2020). X.",
        "Sách và giáo trình",
        "1. B (2019). Y.",
        group_breaks=frozenset({1}),
    ) == [("1", "A (2020). X."), ("1", "B (2019). Y.")]


def test_label_only_lines_give_no_empty_or_padded_entries() -> None:
    assert _pairs(
        "[1]",
        "A. Author, “Title one,” 2020.",
        "[2] B. Author, “Title two,” 2021.",
        "[3]",
    ) == [("1", "A. Author, “Title one,” 2020."), ("2", "B. Author, “Title two,” 2021.")]
    assert _pairs("[1]", "", "  ") == []


def test_join_lines_strips_and_skips_empty_parts() -> None:
    assert join_lines(["", " A. Author,", "", "Title. "]) == "A. Author, Title."
    assert join_lines(["https://doi.org/", "10.1/abcd"]) == "https://doi.org/10.1/abcd"
    assert join_lines(["", " "]) == ""
