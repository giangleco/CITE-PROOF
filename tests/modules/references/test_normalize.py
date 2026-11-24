"""Chuẩn hóa dùng chung cho S2/S3: DOI, gấp dấu, tiêu đề, họ tác giả (spec §7.2)."""

import unicodedata

import pytest

from citeproof.core.domain import ParsedReference
from citeproof.modules.references.normalize import (
    VIETNAMESE_FAMILY_NAMES,
    family_name,
    fold,
    nfc_reference,
    normalize_doi,
    title_norm,
)


@pytest.mark.parametrize(
    ("raw", "doi"),
    [
        ("https://doi.org/10.1000/ABC.", "10.1000/abc"),
        ("doi:10.18653/v1/N19-1423)", "10.18653/v1/n19-1423"),
        ("10.1/x", None),
        ("http://dx.doi.org/10.1109/CVPR.2016.90;", "10.1109/cvpr.2016.90"),
        ("DOI: 10.48550/arXiv.1706.03762]", "10.48550/arxiv.1706.03762"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_doi(raw: str | None, doi: str | None) -> None:
    assert normalize_doi(raw) == doi


def test_fold() -> None:
    assert fold("Nguyễn Đức Đạt") == "nguyen duc dat"
    assert fold("ĐẶNG Thị Hà") == "dang thi ha"
    assert fold(unicodedata.normalize("NFD", "Phạm Minh Đức")) == "pham minh duc"
    assert fold("Tài liệu  tham khảo") == "tai lieu  tham khao"  # không gộp khoảng trắng


def test_title_norm() -> None:
    assert title_norm("BERT: Pre-training  of deep… transformers!") == (
        "bert pre training of deep transformers"
    )
    nfd = unicodedata.normalize("NFD", "Phân loại văn bản tiếng Việt")
    assert title_norm(nfd) == "phân loại văn bản tiếng việt"
    assert title_norm("  “Attention is all you need,” ") == "attention is all you need"
    assert title_norm("snake_case") == "snake case"


@pytest.mark.parametrize(
    ("author", "family"),
    [
        ("Nguyễn Văn An", "Nguyễn"),
        ("Nguyen, V. A.", "Nguyen"),
        ("Ashish Vaswani", "Vaswani"),
        ("A. Vaswani", "Vaswani"),
        ("Trần T. B.", "Trần"),
        ("Vaswani A.", "Vaswani"),
        ("M.-W Chang", "Chang"),
        ("A N Gomez", "Gomez"),
        ("Devlin, J.", "Devlin"),
        ("Phạm Minh Đức", "Phạm"),
        ("Andrew Ng", "Ng"),
        ("Dat Quoc Nguyen", "Nguyen"),
        ("  Lê Hoàng Cường ", "Lê"),
    ],
)
def test_family_name(author: str, family: str) -> None:
    assert family_name(author) == family


def test_vietnamese_family_names() -> None:
    required = {
        "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu",
        "vo", "dang", "bui", "do", "ho", "ngo", "duong", "ly",
    }  # fmt: skip
    assert len(VIETNAMESE_FAMILY_NAMES) >= 30
    assert required <= VIETNAMESE_FAMILY_NAMES
    assert all(fold(name) == name for name in VIETNAMESE_FAMILY_NAMES)
    # Tên đệm/tên gọi phổ biến không được coi là họ.
    assert {"van", "thi", "an", "minh"}.isdisjoint(VIETNAMESE_FAMILY_NAMES)


def test_nfc_reference_normalises_every_text_field() -> None:
    nfd = unicodedata.normalize
    ref = ParsedReference(
        title=nfd("NFD", "Phân loại"),
        authors=[nfd("NFD", "Nguyễn Văn An")],
        year=2020,
        venue=nfd("NFD", "Tạp chí Mẫu"),
        doi="10.1/abcd",
        isbn=None,
        url=None,
        label="1",
    )
    out = nfc_reference(ref)
    assert out.title == "Phân loại"
    assert out.authors == ["Nguyễn Văn An"]
    assert out.venue == "Tạp chí Mẫu"
    assert out.year == 2020 and out.label == "1" and out.doi == "10.1/abcd"
