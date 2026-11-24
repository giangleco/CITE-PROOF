"""Hợp đồng chung của các nguồn bản mở (spec §7.5, QĐ-07).

Một `AccessResolver` chỉ trả các URL bản mở có thể tải cho một work, không tải gì: URL lấy
từ metadata bên thứ ba nên chỉ được tải qua `SafeHttpClient` (S5, `modules.sources.fetch`).
Lỗi của dịch vụ ngoài (`ExternalServiceError`, gồm `RetryableError`) lan ra để S5 bỏ qua
riêng nguồn đó; không có bản mở thì trả danh sách rỗng.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

Provider = Literal["arxiv", "acl", "openalex", "unpaywall"]


@dataclass(frozen=True)
class AccessOption:
    url: str
    license: str | None
    provider: Provider


@dataclass(frozen=True)
class WorkRef:
    """Định danh của một dòng `works` dùng để tìm bản mở (DOI chữ thường, arXiv ID)."""

    work_id: UUID
    doi: str | None
    arxiv_id: str | None


class AccessResolver(Protocol):
    async def resolve(self, work: WorkRef) -> list[AccessOption]: ...
