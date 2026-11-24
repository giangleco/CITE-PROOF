"""Xác định bản mở của tài liệu được trích: arXiv, ACL Anthology, OpenAlex, Unpaywall (S5).

Thứ tự thử cố định (spec §7.5): arXiv, ACL Anthology, OpenAlex `best_oa_location`, rồi
Unpaywall khi người vận hành đặt email liên hệ (QĐ-07). Module này chỉ trả URL; việc tải
(qua `SafeHttpClient`) và lưu thuộc `modules.sources`.
"""

from collections.abc import Callable

import httpx

from citeproof.integrations.metadata import MetadataGateway
from citeproof.integrations.metadata.base import DEFAULT_TIMEOUT_S
from citeproof.integrations.oa.acl import AclAccess
from citeproof.integrations.oa.arxiv import ArxivAccess
from citeproof.integrations.oa.base import AccessOption, AccessResolver, Provider, WorkRef
from citeproof.integrations.oa.openalex_oa import OpenAlexAccess
from citeproof.integrations.oa.unpaywall import UnpaywallAccess


def make_resolvers(
    *,
    http: httpx.AsyncClient,
    gateway_factory: Callable[[], MetadataGateway],
    contact_email: str | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[AccessResolver]:
    """Các nguồn bản mở theo thứ tự thử; Unpaywall chỉ khi có email liên hệ.

    Dựng mới cho mỗi lần chạy: `OpenAlexAccess` giữ một gateway (ngắt mạch theo lần chạy).
    """
    resolvers: list[AccessResolver] = [
        ArxivAccess(),
        AclAccess(),
        OpenAlexAccess(gateway_factory),
    ]
    email = (contact_email or "").strip()
    if email:
        resolvers.append(UnpaywallAccess(http, email, timeout_s=timeout_s))
    return resolvers


__all__ = [
    "AccessOption",
    "AccessResolver",
    "AclAccess",
    "ArxivAccess",
    "OpenAlexAccess",
    "Provider",
    "UnpaywallAccess",
    "WorkRef",
    "make_resolvers",
]
