"""Phân loại tài liệu tham khảo bằng rule (spec §7.2); W1 ghi đè khi được gọi.

Theo thứ tự: arXiv/preprint → PREPRINT; Proceedings/Proc./Conference/Hội nghị/Kỷ yếu/Workshop
→ CONFERENCE; Luận văn/Luận án/Khóa luận/thesis → THESIS; chỉ có URL (không DOI, ISBN) kèm
"truy cập"/"accessed"/"retrieved" → WEB; ISBN/NXB/Nhà xuất bản/Press → BOOK; có tập/số/trang
hoặc tên tạp chí → ARTICLE; còn lại UNKNOWN. So khớp trên chuỗi đã gấp dấu, sau khi bỏ URL và
DOI (chữ số trong định danh không phải số trang).
"""

import re

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import RefType
from citeproof.modules.references.normalize import fold

_IDENTIFIERS = re.compile(r"https?://\S+|\b10\.\d{4,9}/\S+", re.IGNORECASE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_RULES: tuple[tuple[RefType, re.Pattern[str]], ...] = (
    (RefType.PREPRINT, re.compile(r"\b(?:arxiv|biorxiv|medrxiv|preprint)\b")),
    (
        RefType.CONFERENCE,
        re.compile(
            r"\b(?:proceedings|proc\.|conference|hoi nghi|hoi thao|ky yeu|workshop|symposium)"
        ),
    ),
    (
        RefType.THESIS,
        re.compile(r"\b(?:luan van|luan an|khoa luan|thesis|dissertation)\b"),
    ),
)
_WEB_ACCESS = re.compile(r"\b(?:truy cap|accessed|retrieved)\b")
_BOOK = re.compile(r"\b(?:isbn|nxb|nha xuat ban|press|publisher|publishing)\b")
_ARTICLE = re.compile(
    r"\b\d+\s*\(\d+[^)]*\)"  # tập(số): 12(3)
    r"|\b(?:vol|volume|tap|so|no|pp|tr)\.?\s*\d"
    r"|\b\d+\s*[-\u2013]\s*\d+\b"  # khoảng trang
    r"|\b(?:journal|tap chi)\b"
)


def classify_ref_type(raw: str, parsed: ParsedReference) -> RefType:
    """Loại tài liệu theo rule của spec §7.2."""
    folded = fold(_IDENTIFIERS.sub(" ", raw))
    for ref_type, pattern in _RULES:
        if pattern.search(folded):
            return ref_type
    has_url = parsed.url is not None or _URL.search(raw) is not None
    if has_url and parsed.doi is None and parsed.isbn is None and _WEB_ACCESS.search(folded):
        return RefType.WEB
    if parsed.isbn is not None or _BOOK.search(folded):
        return RefType.BOOK
    if _ARTICLE.search(folded):
        return RefType.ARTICLE
    return RefType.UNKNOWN
