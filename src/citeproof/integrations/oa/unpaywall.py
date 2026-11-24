"""Bản mở theo Unpaywall `best_oa_location.url_for_pdf` (spec §7.5, QĐ-07).

Điều khoản của Unpaywall yêu cầu tham số `email`: chỉ dùng khi người vận hành đặt
`CITEPROOF_CONTACT_EMAIL` (không bao giờ là email người dùng). Host API cố định nên gọi qua
`httpx.AsyncClient` dùng chung; ánh xạ lỗi như connector metadata (404 → không có; 429, 5xx,
lỗi mạng → `RetryableError`; lỗi khác → `ExternalServiceError`). URL có email trong query
string: không bao giờ ghi log hay đưa vào thông điệp lỗi.
"""

import httpx

from citeproof.integrations.metadata.base import (
    DEFAULT_TIMEOUT_S,
    as_dict,
    as_str,
    doi_path,
    get_json,
    user_agent,
)
from citeproof.integrations.oa.base import AccessOption, WorkRef

BASE_URL = "https://api.unpaywall.org/v2/"
SOURCE = "unpaywall"


class UnpaywallAccess:
    def __init__(
        self, http: httpx.AsyncClient, email: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._http = http
        self._email = email
        self._timeout_s = timeout_s

    async def resolve(self, work: WorkRef) -> list[AccessOption]:
        if not work.doi:
            return []
        data = await get_json(
            self._http,
            f"{BASE_URL}{doi_path(work.doi)}",
            source=SOURCE,
            timeout_s=self._timeout_s,
            params={"email": self._email},
            headers={"User-Agent": user_agent(self._email)},
        )
        location = as_dict(as_dict(data).get("best_oa_location"))
        pdf_url = as_str(location.get("url_for_pdf"))
        if pdf_url is None:
            return []
        return [
            AccessOption(url=pdf_url, license=as_str(location.get("license")), provider="unpaywall")
        ]
