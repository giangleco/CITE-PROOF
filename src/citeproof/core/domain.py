"""Model miền dùng chung giữa các module (spec §5, §13).

`ParsedReference` là các trường đã tách của một tài liệu tham khảo, dùng cho
`RawReference.parsed` (S1/GROBID), `reference_entries.parsed` (S2) và tra cứu (S3).
`normalize_doi` đặt ở đây để `parsing`, `integrations` và `modules.references` cùng
dùng mà không phụ thuộc chéo giữa các module (preflight F05).
"""

import re

from pydantic import BaseModel, Field

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_DOI_TRAILING = ".,;)]"
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


class ParsedReference(BaseModel):
    """Trường của một tài liệu tham khảo; `authors` giữ nguyên cách viết trong bài."""

    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    isbn: str | None = None
    url: str | None = None
    label: str | None = None


def normalize_doi(text: str | None) -> str | None:
    """DOI chuẩn hóa (spec §7.2): chữ thường, bỏ tiền tố URL/`doi:` và dấu câu cuối.

    Trả `None` khi kết quả không có dạng `10.<4-9 chữ số>/<phần còn lại>`.
    """
    if text is None:
        return None
    value = _DOI_PREFIX.sub("", text.strip()).strip().lower().rstrip(_DOI_TRAILING)
    return value if _DOI.fullmatch(value) else None
