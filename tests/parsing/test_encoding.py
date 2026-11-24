"""Phát hiện bảng mã cũ TCVN3/VNI (spec §7.1, QĐ-05)."""

from citeproof.parsing.docx import parse_docx
from citeproof.parsing.encoding import detect_legacy_encoding
from tests.golden.cases import GOLDEN_DIR


def _docx_text(name: str) -> str:
    return parse_docx((GOLDEN_DIR / f"{name}.docx").read_bytes()).text()


def test_tcvn3_detected() -> None:
    assert detect_legacy_encoding(_docx_text("vi_tcvn3_legacy")) == "TCVN3"
    assert detect_legacy_encoding(_docx_text("vi_thesis_numeric")) is None
    assert detect_legacy_encoding(_docx_text("en_paper_author_year")) is None


def test_vni_detected() -> None:
    assert detect_legacy_encoding("Vieät Nam laø moät nöôùc " * 20) == "VNI"


def test_short_text_with_few_vni_like_pairs_not_flagged() -> None:
    # Dưới 1 000 ký tự vẫn cần đủ 5 cặp (tính trên 1 000 ký tự).
    assert detect_legacy_encoding("Gaël Varoquaux and Loïc Noël wrote a naïve baseline.") is None


def test_english_with_symbols_and_umlauts_not_flagged() -> None:
    # Ký hiệu toán (chấm giữa, dấu nhân, µ, ¬) và tên riêng có umlaut: dày đặc nhưng vẫn
    # thưa so với số chữ cái.
    sentence = (
        "The kernel of Schölkopf and Müller maps a · b to 3 \u00d7 3 cells in 5 µs, ¬p holds, "
        "and the results of the experiments are reported in the following sections. "
    )
    assert detect_legacy_encoding(sentence * 30) is None
