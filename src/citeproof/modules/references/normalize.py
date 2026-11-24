"""Chuẩn hóa dùng chung cho S2, S3, S4: DOI, gấp dấu, tiêu đề, họ tác giả (spec §7.2).

`normalize_doi` nằm ở `core.domain` (preflight F05) và `fold` ở `parsing.references` (bộ tách
danh mục dùng chung với S1); module này xuất lại để các stage chỉ cần một chỗ import.
"""

import re
import unicodedata

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.parsing.references import fold

__all__ = [
    "VIETNAMESE_FAMILY_NAMES",
    "family_name",
    "fold",
    "is_initial",
    "nfc",
    "nfc_reference",
    "normalize_doi",
    "title_norm",
]

# Họ người Việt phổ biến (đã gấp dấu). Không có tên đệm/tên gọi hay gặp ("van", "thi", "an").
VIETNAMESE_FAMILY_NAMES: frozenset[str] = frozenset(
    {
        "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu", "vo", "dang",
        "bui", "do", "ho", "ngo", "duong", "ly", "dinh", "dao", "doan", "truong",
        "trinh", "luong", "luu", "mai", "cao", "lam", "ha", "ta", "quach", "chu",
        "kieu", "la", "tang", "trieu", "vuong", "tong", "ton", "khuc", "nghiem", "phung",
        "giang", "hua", "lai", "dam", "mac", "thach", "ung", "nong", "khong", "au",
    }
)  # fmt: skip

_NON_WORD = re.compile(r"[\W_]+")
_DOTTED_INITIAL = re.compile(r"(?:[^\W\d_]{1,2}\.)+(?:-?[^\W\d_]{1,2}\.?)*")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def title_norm(text: str) -> str:
    """Tiêu đề để so khớp: NFC, chữ thường, dấu câu thành khoảng trắng, gộp khoảng trắng."""
    return _NON_WORD.sub(" ", nfc(text).lower()).strip()


def is_initial(token: str) -> bool:
    """Chữ viết tắt tên: "A.", "M.-W", "Th.", hoặc chữ hoa không dấu chấm tối đa 3 ký tự ("AN")."""
    value = token.strip(",;")
    if not value:
        return False
    if "." in value:
        return _DOTTED_INITIAL.fullmatch(value) is not None
    return value.isalpha() and value.isupper() and len(value) <= 3


def family_name(author: str) -> str:
    """Họ của một tác giả viết như trong bài.

    Phần trước dấu phẩy ("Nguyen, V. A."); không có dấu phẩy thì từ đầu nếu là họ người Việt
    ("Nguyễn Văn An", "Trần T. B."); còn lại là từ cuối không phải chữ viết tắt ("A. Vaswani").
    """
    text = author.strip()
    if "," in text:
        head = text.split(",", 1)[0].strip()
        if head:
            return head
    tokens = text.split()
    if not tokens:
        return text
    if fold(tokens[0].strip(",;.")) in VIETNAMESE_FAMILY_NAMES:
        return tokens[0].strip(",;")
    for token in reversed(tokens):
        if not is_initial(token):
            return token.strip(",;")
    return tokens[-1].strip(",;")


def nfc_reference(ref: ParsedReference) -> ParsedReference:
    """Mọi trường chữ ở dạng NFC (trường từ TEI, rule hay W1 đều đi qua đây)."""

    def norm(value: str | None) -> str | None:
        return None if value is None else nfc(value)

    return ParsedReference(
        title=norm(ref.title),
        authors=[nfc(author) for author in ref.authors],
        year=ref.year,
        venue=norm(ref.venue),
        doi=norm(ref.doi),
        isbn=norm(ref.isbn),
        url=norm(ref.url),
        label=norm(ref.label),
    )
