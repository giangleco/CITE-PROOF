"""Khớp tiêu đề của PDF người dùng tải lên với tài liệu tham khảo (FR-33, spec lõi §13).

Văn bản so khớp gồm tiêu đề trong metadata PDF và văn bản hai trang đầu; so bằng
`partial_ratio` trên `fold(title_norm(...))`. Không có tiêu đề để so thì `UNKNOWN`.
"""

from rapidfuzz import fuzz

from citeproof.core.enums import TitleMatch
from citeproof.modules.references.normalize import title_norm
from citeproof.parsing.pdf import pdf_metadata_title, pdf_page_texts
from citeproof.parsing.references import fold

TITLE_MATCH_THRESHOLD = 85
_FIRST_PAGES = 2


def _key(text: str) -> str:
    return fold(title_norm(text))


def title_match(pdf: bytes, title: str | None) -> TitleMatch:
    wanted = _key(title or "")
    if not wanted:
        return TitleMatch.UNKNOWN
    parts = [pdf_metadata_title(pdf) or "", *pdf_page_texts(pdf, max_pages=_FIRST_PAGES)]
    text = _key("\n".join(parts))
    if text and fuzz.partial_ratio(wanted, text) >= TITLE_MATCH_THRESHOLD:
        return TitleMatch.MATCH
    return TitleMatch.MISMATCH
