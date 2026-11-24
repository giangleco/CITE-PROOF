"""Phát hiện marker trích dẫn (spec §7.4; Review Focus #3: ngoặc không phải trích dẫn)."""

import itertools

import pytest

from citeproof.core.enums import LinkMethod
from citeproof.modules.citations.markers import AuthorYear, find_markers


@pytest.mark.parametrize(
    ("s", "numbers"),
    [
        ("A [1].", (1,)),
        ("A [1\u20133].", (1, 2, 3)),
        ("A [1, 4].", (1, 4)),
        ("A [2-4, 7].", (2, 3, 4, 7)),
    ],
)
def test_numeric_markers(s: str, numbers: tuple[int, ...]) -> None:
    markers = find_markers(s)
    assert len(markers) == 1
    marker = markers[0]
    assert marker.kind is LinkMethod.NUMERIC
    assert marker.numbers == numbers
    assert marker.cites == ()
    assert s[marker.start : marker.end] == marker.text
    assert marker.text.startswith("[") and marker.text.endswith("]")


def test_numeric_marker_variants() -> None:
    (marker,) = find_markers("Kết quả [1; 3 \u2013 5,\n8].")
    assert marker.numbers == (1, 3, 4, 5, 8)
    # Khoảng IEEE viết bằng hai ngoặc: một marker.
    for text in ("Đã được dùng rộng rãi [2]\u2013[4].", "Widely adopted [2]- [4]."):
        (marker,) = find_markers(text)
        assert marker.numbers == (2, 3, 4)
        assert text[marker.start : marker.end] == marker.text
        assert marker.text.startswith("[2]") and marker.text.endswith("[4]")
    # Hai ngoặc cạnh nhau không nối bằng gạch là hai marker.
    assert [m.numbers for m in find_markers("A [1], [3] và B [5][6].")] == [(1,), (3,), (5,), (6,)]


@pytest.mark.parametrize(
    ("s", "numbers"),
    [
        ("Kết quả [1, tr. 25].", (1,)),
        ("Kết quả [1, trang 25].", (1,)),
        ("Kết quả [1, tr.25-30].", (1,)),
        ("Result [1, p. 25].", (1,)),
        ("Result [1, pp. 25\u201330].", (1,)),
        ("Kết quả [1: 25].", (1,)),
        ("Kết quả [1:25-30].", (1,)),
        ("Kết quả [1, tr. 25; 3, tr. 7].", (1, 3)),
        ("Kết quả [1, tr. 25; 3].", (1, 3)),
        ("Kết quả [2, 4, tr. 9].", (2, 4)),
        ("Kết quả [12, Tr. 5].", (12,)),
        # Sau vị trí trang, số còn lại trong nhóm là trang, không phải tài liệu 30.
        ("Kết quả [1, tr. 25, 30].", (1,)),
        ("Kết quả [1: 25, 30; 4].", (1, 4)),
        # Số đứng ngay trước vị trí trang là số tài liệu, kể cả khi nối bằng dấu phẩy.
        ("Kết quả [1, tr. 25, 3, tr. 7].", (1, 3)),
        ("Kết quả [1, tr. 25, 30, 3, tr. 7].", (1, 3)),
        ("Kết quả [1: 25, 3: 7].", (1, 3)),
    ],
)
def test_page_locator_markers(s: str, numbers: tuple[int, ...]) -> None:
    """Số trang (`tr.`, `trang`, `p.`, `pp.`, `:`) không phải số tài liệu; marker giữ cả ngoặc."""
    (marker,) = find_markers(s)
    assert marker.kind is LinkMethod.NUMERIC
    assert marker.numbers == numbers
    assert s[marker.start : marker.end] == marker.text
    assert marker.text == s[s.index("[") : s.index("]") + 1]


def test_numeric_duplicates_and_order_kept() -> None:
    (marker,) = find_markers("A [3, 1, 3].")
    assert marker.numbers == (3, 1, 3)


def test_author_year_markers() -> None:
    (marker,) = find_markers("Mô hình này rất phổ biến (Vaswani et al., 2017).")
    assert marker.kind is LinkMethod.AUTHOR_YEAR
    assert marker.text == "(Vaswani et al., 2017)"
    assert marker.numbers == ()
    assert marker.cites == (AuthorYear(name="Vaswani", year=2017, suffix=None),)

    text = "Nhiều nghiên cứu đồng ý (Nguyễn Văn An, 2020; Lê, 2019a)."
    (marker,) = find_markers(text)
    assert marker.text == "(Nguyễn Văn An, 2020; Lê, 2019a)"
    assert text[marker.start : marker.end] == marker.text
    assert marker.cites == (
        AuthorYear(name="Nguyễn Văn An", year=2020, suffix=None),
        AuthorYear(name="Lê", year=2019, suffix="a"),
    )

    text = "Nguyễn Văn An (2020) cho rằng mô hình A tốt hơn."
    (marker,) = find_markers(text)
    assert marker.kind is LinkMethod.AUTHOR_YEAR
    assert marker.text == "Nguyễn Văn An (2020)"
    assert marker.start == 0
    assert marker.cites == (AuthorYear(name="Nguyễn Văn An", year=2020, suffix=None),)


@pytest.mark.parametrize(
    ("text", "name", "marker_text"),
    [
        ("(Hochreiter & Schmidhuber, 1997)", "Hochreiter", "(Hochreiter & Schmidhuber, 1997)"),
        ("(Trần Thị Bích và cs., 2021)", "Trần Thị Bích", "(Trần Thị Bích và cs., 2021)"),
        ("(Lê và Trần, 2019)", "Lê", "(Lê và Trần, 2019)"),
        ("(van der Maaten, 2008)", "van der Maaten", "(van der Maaten, 2008)"),
        (
            "(Bộ Giáo dục và Đào tạo, 2018)",
            "Bộ Giáo dục và Đào tạo",
            "(Bộ Giáo dục và Đào tạo, 2018)",
        ),
        ("Theo Nguyễn và cs. (2020), mô hình A tốt hơn.", "Nguyễn", "Nguyễn và cs. (2020)"),
        ("In Vaswani et al. (2017) the model", "Vaswani", "Vaswani et al. (2017)"),
        ("Smith and Jones (2010) argue", "Smith", "Smith and Jones (2010)"),
        (
            "Vaswani et\nal., 2017 aside (Vaswani et\nal., 2017).",
            "Vaswani",
            "(Vaswani et\nal., 2017)",
        ),
    ],
)
def test_author_year_name_forms(text: str, name: str, marker_text: str) -> None:
    (marker,) = find_markers(text)
    assert marker.text == marker_text
    assert text[marker.start : marker.end] == marker_text
    assert marker.cites[0].name == name


def test_author_year_page_and_multiple_years() -> None:
    (marker,) = find_markers("Như đã nêu (Smith, 2010, tr. 25; Jones, 2011b, p. 3).")
    assert [(c.name, c.year, c.suffix) for c in marker.cites] == [
        ("Smith", 2010, None),
        ("Jones", 2011, "b"),
    ]
    (marker,) = find_markers("Lê Văn Cường (2019a, 2019b) đã chỉ ra.")
    assert [(c.name, c.year, c.suffix) for c in marker.cites] == [
        ("Lê Văn Cường", 2019, "a"),
        ("Lê Văn Cường", 2019, "b"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Dữ liệu năm [2019] được dùng.",
        "Mẫu gồm [n = 30] người.",
        "Điểm [x, y] nằm trong miền.",
        "Khoảng [0, 1] là miền giá trị.",
        "Khoảng [1-1000] quá dài.",
        "Ngoặc rỗng [] và [ , ] và [1-] và [-3] và [1--3].",
        "Năm (2019) là năm tốt.",
        "Số mẫu (n = 30, 2020) đủ lớn.",
        "Theo bảng (xem Bảng 2, 2020).",
        "Ghi chú (trong năm 2020) và (1799) và (Smith, 1799) và (Smith, 2100).",
        "Thiết bị COVID-19 (2020) mới.",
        "Khoảng [5-3] bị đảo.",
        # Chỉ có số trang, không có số tài liệu: không phải trích dẫn.
        "Xem [tr. 25] của giáo trình.",
        "Xem [trang 25] và [p. 5] và [pp. 3-4].",
        "Lỗi [1, tr.] và [1, tr. 25, tr. 30] và [tr. 25; 3, tr. 7] và [1:] và [: 25].",
        "Năm [2019, tr. 5] và mẫu [n = 30, tr. 5] và [x, tr. 5].",
    ],
)
def test_bracket_non_markers_ignored(text: str) -> None:
    assert find_markers(text) == []


def test_markers_sorted_and_not_overlapping() -> None:
    text = "A improves B [1], while C degrades D (Smith, 2010) and E (2011) [2]."
    markers = find_markers(text)
    assert [m.text for m in markers] == ["[1]", "(Smith, 2010)", "[2]"]
    assert all(a.end <= b.start for a, b in itertools.pairwise(markers))
