"""Connector Crossref REST API (spec §7.3).

`/works/{doi}` tra theo DOI; `/works?query.bibliographic=…` tìm theo chuỗi thư mục. DOI
đăng ký ở DataCite (ví dụ arXiv `10.48550/…`) không có trong Crossref: API trả 404 và
cascade chuyển sang OpenAlex. Polite pool: email người vận hành trong User-Agent.
"""

from typing import Any

import httpx

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.integrations.metadata.abstracts import clean_jats
from citeproof.integrations.metadata.base import (
    DEFAULT_TIMEOUT_S,
    arxiv_id,
    as_dict,
    as_list,
    as_str,
    as_year,
    bad_envelope,
    bibliographic_query,
    doi_path,
    first_str,
    get_json,
    parse_record,
    parse_records,
    search_fields,
    user_agent,
    year_window,
)
from citeproof.integrations.metadata.models import WorkAuthor, WorkRecord

BASE_URL = "https://api.crossref.org"
# Trường cần cho `WorkRecord`; route danh sách không cho chọn `language`.
SELECT = "DOI,title,author,issued,published,container-title,type,ISBN,abstract"
_DATE_FIELDS = ("issued", "published", "published-print", "published-online")


class CrossrefConnector:
    name = "crossref"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        contact_email: str | None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._headers = {"User-Agent": user_agent(contact_email)}
        self._timeout_s = timeout_s

    async def get_by_doi(self, doi: str) -> WorkRecord | None:
        data = await self._get(f"{BASE_URL}/works/{doi_path(doi)}")
        if data is None:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
            raise bad_envelope(self.name)
        return parse_record(data["message"], parse_work)

    async def search(self, ref: ParsedReference, limit: int = 5) -> list[WorkRecord]:
        query = bibliographic_query(search_fields(ref))
        return await self._search(query, year=None, limit=limit)

    async def search_text(
        self, query: str, *, year: int | None = None, limit: int = 5
    ) -> list[WorkRecord]:
        return await self._search(query, year=year, limit=limit)

    async def _search(self, query: str, *, year: int | None, limit: int) -> list[WorkRecord]:
        if not query.strip():
            return []
        params = {"query.bibliographic": query, "rows": str(limit), "select": SELECT}
        if year is not None:
            start, end = year_window(year)
            params["filter"] = f"from-pub-date:{start},until-pub-date:{end}"
        data = await self._get(f"{BASE_URL}/works", params=params)
        if data is None:
            return []
        items = as_dict(as_dict(data).get("message")).get("items")
        if not isinstance(items, list):
            raise bad_envelope(self.name)
        return parse_records(items, parse_work)

    async def _get(self, url: str, params: dict[str, str] | None = None) -> Any | None:
        return await get_json(
            self._http,
            url,
            source=self.name,
            timeout_s=self._timeout_s,
            params=params,
            headers=self._headers,
        )


def parse_work(item: dict[str, Any]) -> WorkRecord | None:
    """Một work Crossref; không có tiêu đề hoặc DOI hợp lệ → None."""
    title = _clean(first_str(item.get("title")))
    doi = normalize_doi(as_str(item.get("DOI")))
    if title is None or doi is None:
        return None
    return WorkRecord(
        source="crossref",
        source_id=doi,
        title=" ".join(title.split()),
        authors=_authors(item.get("author")),
        year=_year(item),
        venue=_clean(first_str(item.get("container-title"))),
        doi=doi,
        arxiv_id=arxiv_id(doi),
        isbn=first_str(item.get("ISBN")),
        abstract=_clean(as_str(item.get("abstract"))),
        language=as_str(item.get("language")),
        work_type=as_str(item.get("type")),
    )


def _clean(text: str | None) -> str | None:
    """Bỏ thẻ JATS/HTML (tiêu đề có `<i>`, abstract là JATS); rỗng sau khi bỏ → None."""
    if text is None:
        return None
    return clean_jats(text) or None


def _authors(value: Any) -> list[WorkAuthor]:
    authors: list[WorkAuthor] = []
    for entry in as_list(value):
        data = as_dict(entry)
        # Tác giả là tổ chức chỉ có `name`.
        family = as_str(data.get("family")) or as_str(data.get("name"))
        if family:
            authors.append(WorkAuthor(family=family, given=as_str(data.get("given"))))
    return authors


def _year(item: dict[str, Any]) -> int | None:
    for field in _DATE_FIELDS:
        parts = as_list(as_dict(item.get(field)).get("date-parts"))
        first = as_list(parts[0]) if parts else []
        year = as_year(first[0]) if first else None
        if year is not None:
            return year
    return None
