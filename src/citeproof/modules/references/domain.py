"""Cảnh báo ngoài lĩnh vực (FR-05, QĐ-41): heuristic rẻ theo lĩnh vực OpenAlex của các tài
liệu tham khảo đã khớp.

Ít nhất `MIN_WORKS_WITH_FIELD` tài liệu đã khớp có lĩnh vực: tỷ lệ "Computer Science" dưới
`CS_MIN_SHARE` → `out_of_domain`, ngược lại `in_scope`; chưa đủ dữ liệu → `unknown`.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from citeproof.core.enums import DomainFlag

OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
MIN_WORKS_WITH_FIELD = 5
CS_FIELD = "Computer Science"
CS_MIN_SHARE = 0.25


def domain_flag(fields: Sequence[str | None]) -> DomainFlag:
    """Cờ lĩnh vực từ lĩnh vực OpenAlex của các tài liệu đã khớp (None: không có lĩnh vực)."""
    known = [value.strip().casefold() for value in fields if value and value.strip()]
    if len(known) < MIN_WORKS_WITH_FIELD:
        return DomainFlag.UNKNOWN
    share = sum(1 for value in known if value == CS_FIELD.casefold()) / len(known)
    return DomainFlag.IN_SCOPE if share >= CS_MIN_SHARE else DomainFlag.OUT_OF_DOMAIN


def with_domain_warning(warnings: Sequence[Any], flag: DomainFlag) -> list[Any]:
    """`parse_warnings` có `{"code": "OUT_OF_DOMAIN"}` đúng khi cờ là `out_of_domain`."""
    kept = [
        item
        for item in warnings
        if not (isinstance(item, Mapping) and item.get("code") == OUT_OF_DOMAIN)
    ]
    if flag is DomainFlag.OUT_OF_DOMAIN:
        kept.append({"code": OUT_OF_DOMAIN})
    return kept
