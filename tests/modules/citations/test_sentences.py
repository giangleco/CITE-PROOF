"""Tách câu cho S4 (spec §7.4): `[.!?…]` + khoảng trắng + chữ hoa/số/ngoặc, trừ viết tắt và
số thập phân; vị trí tính trong đoạn.
"""

import pytest

from citeproof.modules.citations.sentences import Sentence, split_sentences


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("Theo Nguyễn và cs. (2020), mô hình A tốt hơn. Kết quả khác.", 2),
        ("Vaswani et al. propose X. It works.", 2),
        ("Độ chính xác đạt 3.5 điểm. Tiếp theo.", 2),
        ("See Fig. 2 for details. Next.", 2),
    ],
)
def test_split_sentences(text: str, count: int) -> None:
    sentences = split_sentences(text)
    assert len(sentences) == count
    for sentence in sentences:
        assert text[sentence.start : sentence.end] == sentence.text


def test_offsets_are_paragraph_relative_and_trimmed() -> None:
    text = "  Câu thứ nhất [1].   Câu thứ hai [2]!\nCâu thứ ba? 2020 là năm mới…  "
    sentences = split_sentences(text)
    assert [s.text for s in sentences] == [
        "Câu thứ nhất [1].",
        "Câu thứ hai [2]!",
        "Câu thứ ba?",
        "2020 là năm mới…",
    ]
    assert sentences[0] == Sentence(text="Câu thứ nhất [1].", start=2, end=19)
    for sentence in sentences:
        assert text[sentence.start : sentence.end] == sentence.text


@pytest.mark.parametrize(
    "text",
    [
        "Theo TS. Nguyễn Văn An, mô hình tốt hơn.",
        "Kết quả ở tr. 45 cho thấy điều đó.",
        "Các mô hình CNN, RNN, v.v. Đều được thử.",
        "Xem Eq. 3 và Vol. 2, No. 4 của tạp chí.",
        "Một số ví dụ (e.g. BERT) và (i.e. Transformer) được dùng.",
        "PGS. Trần Thị Bích và GS. Lê Văn Cường cùng ThS. Phạm Đức đã viết.",
        "So sánh A vs. B trong pp. 10-12.",
    ],
)
def test_abbreviations_do_not_split(text: str) -> None:
    assert [s.text for s in split_sentences(text)] == [text]


def test_split_before_bracket_quote_and_digit() -> None:
    text = 'Câu một. (Câu hai.) "Câu ba." [4] là câu bốn. 5 người tham gia.'
    assert [s.text for s in split_sentences(text)] == [
        "Câu một.",
        "(Câu hai.)",
        '"Câu ba."',
        "[4] là câu bốn.",
        "5 người tham gia.",
    ]


def test_no_split_before_lowercase_and_line_breaks_are_spaces() -> None:
    text = "Mô hình đạt 90%. tiếp tục chạy.\nSau đó dừng."
    assert [s.text for s in split_sentences(text)] == [
        "Mô hình đạt 90%. tiếp tục chạy.",
        "Sau đó dừng.",
    ]


def test_marker_after_period_stays_with_its_sentence() -> None:
    text = "Prior work studied this.[1] Next sentence follows. Another one. [2] Last one."
    assert [s.text for s in split_sentences(text)] == [
        "Prior work studied this.[1]",
        "Next sentence follows.",
        "Another one. [2]",
        "Last one.",
    ]


def test_empty_and_blank_text() -> None:
    assert split_sentences("") == []
    assert split_sentences("   \n ") == []
    assert split_sentences("Không có dấu chấm cuối") == [
        Sentence(text="Không có dấu chấm cuối", start=0, end=22)
    ]
