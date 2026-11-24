"""Heuristic ngôn ngữ không phụ thuộc thư viện (QĐ-05, spec §7.1, §7.8).

- Một đoạn là `vi` khi chữ có dấu tiếng Việt chiếm ≥ 8% số chữ cái và có ít nhất một
  chữ chỉ tiếng Việt mới có (ă, đ, ơ, ư, ạ, ế…). Điều kiện sau loại văn bản bảng mã
  cũ (TCVN3/VNI đọc như Latin-1 chỉ sinh chữ Latin-1 như è, Ö) và tiếng Pháp.
- `en` khi chữ có dấu tiếng Việt < 1% và có từ chức năng tiếng Anh: ≥ 2 từ, hoặc 1 từ
  với đoạn ngắn (dưới 8 từ).
- Tài liệu `mixed` khi `vi` và `en` mỗi bên chiếm ≥ 20% số ký tự của mọi đoạn; ngược
  lại là ngôn ngữ chiếm nhiều ký tự hơn, không có thì `unknown`.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from citeproof.core.enums import Language

if TYPE_CHECKING:
    from citeproof.parsing.model import Paragraph

SourceLanguage = Literal["vi", "en", "mixed", "unknown", "other"]

VI_MIN_SHARE = 0.08
EN_MAX_VI_SHARE = 0.01
EN_MIN_FUNCTION_WORDS = 2
SHORT_TEXT_WORDS = 8
MIXED_MIN_SHARE = 0.20
OTHER_MIN_NON_LATIN_SHARE = 0.50

# Chữ có dấu chỉ có trong tiếng Việt: ă đ ĩ ũ ơ ư và khối Latin Extended Additional.
_VI_SPECIFIC = frozenset("ăđĩũơư") | frozenset(chr(c) for c in range(0x1EA1, 0x1EFA, 2))
# Chữ có dấu tiếng Việt nằm trong Latin-1 (cũng gặp ở tiếng Pháp, Tây Ban Nha…).
_VI_LATIN1 = frozenset("àáâãèéêìíòóôõùúý")
_VI_LETTERS = _VI_SPECIFIC | _VI_LATIN1
# Từ chức năng tiếng Anh không trùng âm tiết tiếng Việt không dấu ("an", "to", "do"…).
_EN_FUNCTION_WORDS = frozenset(
    {
        "the", "of", "and", "is", "are", "was", "were", "be", "been", "for", "with", "that",
        "this", "these", "those", "which", "from", "by", "on", "at", "as", "it", "its", "we",
        "our", "they", "their", "has", "have", "had", "not", "or", "but", "can", "will",
        "would", "should", "than", "into", "such", "also", "in", "there", "between",
    }
)  # fmt: skip
_WORD = re.compile(r"[a-z]+")
_SOURCE_LANGUAGES: Mapping[Language, SourceLanguage] = MappingProxyType(
    {
        Language.VI: "vi",
        Language.EN: "en",
        Language.MIXED: "mixed",
        Language.UNKNOWN: "unknown",
    }
)


def _letters(text: str) -> list[str]:
    return [char for char in unicodedata.normalize("NFC", text).lower() if char.isalpha()]


def detect_language(text: str) -> Language:
    """Ngôn ngữ của một đoạn: `vi`, `en` hoặc `unknown` (không bao giờ `mixed`)."""
    letters = _letters(text)
    if not letters:
        return Language.UNKNOWN
    vi_letters = [char for char in letters if char in _VI_LETTERS]
    vi_share = len(vi_letters) / len(letters)
    if vi_share >= VI_MIN_SHARE and any(char in _VI_SPECIFIC for char in vi_letters):
        return Language.VI
    if vi_share < EN_MAX_VI_SHARE:
        words = _WORD.findall(text.lower())
        needed = EN_MIN_FUNCTION_WORDS if len(words) >= SHORT_TEXT_WORDS else 1
        if sum(word in _EN_FUNCTION_WORDS for word in words) >= needed:
            return Language.EN
    return Language.UNKNOWN


def document_language(paragraphs: Sequence["Paragraph"]) -> Language:
    """Ngôn ngữ tài liệu theo số ký tự của các đoạn `vi`/`en`."""
    total = sum(len(p.text) for p in paragraphs)
    vi = sum(len(p.text) for p in paragraphs if p.language is Language.VI)
    en = sum(len(p.text) for p in paragraphs if p.language is Language.EN)
    if total == 0 or vi + en == 0:
        return Language.UNKNOWN
    if vi >= MIXED_MIN_SHARE * total and en >= MIXED_MIN_SHARE * total:
        return Language.MIXED
    return Language.VI if vi >= en else Language.EN


def source_language(text: str) -> SourceLanguage:
    """Ngôn ngữ của nguồn: `other` khi chữ không phải Latin chiếm > 50% số chữ cái."""
    letters = _letters(text)
    non_latin = sum(not unicodedata.name(char, "").startswith("LATIN") for char in letters)
    if letters and non_latin > OTHER_MIN_NON_LATIN_SHARE * len(letters):
        return "other"
    return _SOURCE_LANGUAGES[detect_language(text)]
