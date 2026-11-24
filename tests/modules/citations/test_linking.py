"""Liên kết marker với mục tài liệu tham khảo (spec §7.4, TK-14; Review Focus #3).

Không bao giờ liên kết sai: số ngoài khoảng, nhãn trùng, tác giả-năm nhiều ứng viên hay tên
đầy đủ khác người đều cho link NULL.
"""

import uuid
from collections.abc import Sequence

from citeproof.core.domain import ParsedReference
from citeproof.modules.citations.linking import (
    LinkTarget,
    ReferenceIndex,
    make_target,
    year_suffix,
)
from citeproof.modules.citations.markers import Marker, find_markers


def _target(
    order_index: int,
    *,
    label: str | None = None,
    authors: Sequence[str] = (),
    year: int | None = None,
    suffix: str | None = None,
) -> LinkTarget:
    return LinkTarget(
        entry_id=uuid.uuid4(),
        order_index=order_index,
        label=label,
        authors=tuple(authors),
        year=year,
        year_suffix=suffix,
    )


def _marker(text: str) -> Marker:
    (marker,) = find_markers(text)
    return marker


def test_numeric_marker_out_of_range_is_unresolved() -> None:
    targets = [_target(i) for i in range(40)]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [57].")) == [None]
    assert index.link(_marker("A [40].")) == [targets[39].entry_id]
    assert index.link(_marker("A [39\u201341].")) == [
        targets[38].entry_id,
        targets[39].entry_id,
        None,
    ]


def test_numeric_link_uses_label() -> None:
    targets = [_target(0, label="3"), _target(1, label="4")]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [4].")) == [targets[1].entry_id]
    assert index.link(_marker("A [1].")) == [None]  # có nhãn: không dùng thứ tự


def test_numeric_link_without_labels_uses_order() -> None:
    targets = [_target(0), _target(1), _target(2)]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [1, 3].")) == [targets[0].entry_id, targets[2].entry_id]


def test_stray_label_in_unlabelled_list_keeps_order() -> None:
    # Danh mục APA (không nhãn) có một mục lạc nhãn: vẫn đánh số theo thứ tự.
    targets = [_target(0), _target(1, label="7"), _target(2)]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [1, 3].")) == [targets[0].entry_id, targets[2].entry_id]
    # Phần lớn mục có nhãn: mục thiếu nhãn không được đoán theo thứ tự.
    targets = [_target(0, label="1"), _target(1), _target(2, label="3")]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [1, 2, 3].")) == [targets[0].entry_id, None, targets[2].entry_id]


def test_duplicate_labels_are_ambiguous() -> None:
    # Danh mục đánh số lại theo nhóm: "[1]" trỏ tới hai mục → không đoán.
    targets = [_target(0, label="1"), _target(1, label="2"), _target(2, label="1")]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [1, 2].")) == [None, targets[1].entry_id]


def test_page_locator_links_reference_numbers_only() -> None:
    targets = [_target(i) for i in range(40)]
    index = ReferenceIndex(targets)
    # Số trang 25 không bao giờ thành link tới mục 25.
    assert index.link(_marker("A [1, tr. 25].")) == [targets[0].entry_id]
    assert index.link(_marker("A [3: 25-30].")) == [targets[2].entry_id]
    assert index.link(_marker("A [1, tr. 25; 57, tr. 7].")) == [targets[0].entry_id, None]


def test_numeric_link_keeps_one_item_per_number() -> None:
    targets = [_target(0), _target(1)]
    index = ReferenceIndex(targets)
    assert index.link(_marker("A [2, 2].")) == [targets[1].entry_id, targets[1].entry_id]


def test_link_vietnamese_full_name_unique() -> None:
    an = _target(0, authors=["Nguyễn Văn An"], year=2020)
    binh = _target(1, authors=["Nguyễn Thị Bình"], year=2020)
    index = ReferenceIndex([an, binh])
    assert index.link(_marker("Đã có (Nguyễn Văn An, 2020).")) == [an.entry_id]
    assert index.link(_marker("Nguyễn Thị Bình (2020) cho rằng.")) == [binh.entry_id]


def test_link_ambiguous_family_year_unresolved() -> None:
    an = _target(0, authors=["Nguyễn Văn An"], year=2020)
    binh = _target(1, authors=["Nguyễn Thị Bình"], year=2020)
    index = ReferenceIndex([an, binh])
    assert index.link(_marker("Đã có (Nguyễn, 2020).")) == [None]


def test_link_family_and_year_unique() -> None:
    vaswani = _target(0, authors=["Vaswani, A.", "Shazeer, N."], year=2017)
    devlin = _target(1, authors=["J. Devlin", "M.-W. Chang"], year=2019)
    nguyen = _target(2, authors=["Nguyen, V. A."], year=2020)
    index = ReferenceIndex([vaswani, devlin, nguyen])
    marker = _marker("Cả hai (Vaswani et al., 2017; Devlin et al., 2019).")
    assert index.link(marker) == [vaswani.entry_id, devlin.entry_id]
    assert index.link(_marker("Như trên (Vaswani et al., 2018).")) == [None]  # năm khác
    # Tên đầy đủ có dấu khớp tên viết tắt không dấu trong danh mục.
    assert index.link(_marker("Đã có (Nguyễn Văn An, 2020).")) == [nguyen.entry_id]


def test_full_name_of_another_person_is_not_linked() -> None:
    binh = _target(0, authors=["Nguyễn Thị Bình"], year=2020)
    index = ReferenceIndex([binh])
    assert index.link(_marker("Đã có (Nguyễn Văn An, 2020).")) == [None]
    initials = _target(0, authors=["Nguyễn T. B."], year=2020)
    assert ReferenceIndex([initials]).link(_marker("Đã có (Nguyễn Văn An, 2020).")) == [None]


def test_year_suffix_disambiguates() -> None:
    first = _target(0, authors=["Lê Hoàng Cường"], year=2019, suffix="a")
    second = _target(1, authors=["Lê Hoàng Cường"], year=2019, suffix="b")
    index = ReferenceIndex([first, second])
    assert index.link(_marker("Đã có (Lê, 2019b).")) == [second.entry_id]
    assert index.link(_marker("Đã có (Lê, 2019a).")) == [first.entry_id]
    assert index.link(_marker("Đã có (Lê, 2019).")) == [None]


def test_organisation_author() -> None:
    org = _target(0, authors=["Bộ Giáo dục và Đào tạo"], year=2018)
    index = ReferenceIndex([org])
    assert index.link(_marker("Theo quy định (Bộ Giáo dục và Đào tạo, 2018).")) == [org.entry_id]


def test_entries_without_authors_or_year_never_match() -> None:
    index = ReferenceIndex([_target(0, year=2020), _target(1, authors=["Nguyễn Văn An"])])
    assert index.link(_marker("Đã có (Nguyễn Văn An, 2020).")) == [None]


def test_empty_index() -> None:
    index = ReferenceIndex([])
    assert index.link(_marker("A [1].")) == [None]
    assert index.link(_marker("Đã có (Smith, 2010).")) == [None]


def test_year_suffix_from_raw_text() -> None:
    assert year_suffix("Lê H. C. (2019a), Giáo trình, NXB.", 2019) == "a"
    assert year_suffix("Lê H. C. (2019), Giáo trình 2019b.", 2019) is None
    assert year_suffix("Smith, J. 2010b. Title.", 2010) == "b"
    assert year_suffix("Không có năm", None) is None


def test_make_target_reads_parsed_fields() -> None:
    entry_id = uuid.uuid4()
    parsed = ParsedReference(title="T", authors=["Lê Hoàng Cường"], year=2019, label="4")
    target = make_target(entry_id, 3, parsed.model_dump(mode="json"), "Lê Hoàng Cường (2019a), T.")
    assert target == LinkTarget(
        entry_id=entry_id,
        order_index=3,
        label="4",
        authors=("Lê Hoàng Cường",),
        year=2019,
        year_suffix="a",
    )
    empty = make_target(entry_id, 0, {}, "")
    assert empty.authors == () and empty.year is None and empty.label is None
