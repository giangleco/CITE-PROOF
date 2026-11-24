"""Connector OpenAlex (spec §7.3, QĐ-07, QĐ-41).

`/works/doi:{doi}` tra theo DOI; `/works?search=…` tìm toàn văn. `field` lấy từ
`primary_topic.field.display_name` (cảnh báo ngoài lĩnh vực); `oa` từ `best_oa_location`
khi có `pdf_url`; arXiv ID từ `ids.arxiv` (bỏ phiên bản). Polite pool: tham số `mailto`
và User-Agent khi người vận hành đặt email. Khóa API (tùy chọn) đi trong tham số `api_key`:
URL kèm query string không bao giờ được ghi log hay đưa vào thông điệp lỗi.
"""

from typing import Any

import httpx

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.integrations.metadata.abstracts import abstract_from_inverted_index
from citeproof.integrations.metadata.base import (
    DEFAULT_TIMEOUT_S,
    arxiv_id,
    as_dict,
    as_list,
    as_str,
    as_year,
    author_from_name,
    bad_envelope,
    doi_path,
    get_json,
    parse_record,
    parse_records,
    search_fields,
    title_query,
    user_agent,
    year_window,
)
from citeproof.integrations.metadata.models import OaLocation, WorkAuthor, WorkRecord

BASE_URL = "https://api.openalex.org"
SELECT = (
    "id,doi,title,display_name,publication_year,language,type,ids,authorships,"
    "primary_location,best_oa_location,primary_topic,abstract_inverted_index"
)
_ID_PREFIX = "https://openalex.org/"


class OpenAlexConnector:
    name = "openalex"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        contact_email: str | None,
        api_key: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._headers = {"User-Agent": user_agent(contact_email)}
        self._base_params = {"select": SELECT}
        if contact_email:
            self._base_params["mailto"] = contact_email
        if api_key:
            self._base_params["api_key"] = api_key
        self._timeout_s = timeout_s

    async def get_by_doi(self, doi: str) -> WorkRecord | None:
        data = await self._get(f"{BASE_URL}/works/doi:{doi_path(doi)}")
        if data is None:
            return None
        if not isinstance(data, dict):
            raise bad_envelope(self.name)
        return parse_record(data, parse_work)

    async def search(self, ref: ParsedReference, limit: int = 5) -> list[WorkRecord]:
        return await self._search(title_query(search_fields(ref)), year=None, limit=limit)

    async def search_text(
        self, query: str, *, year: int | None = None, limit: int = 5
    ) -> list[WorkRecord]:
        return await self._search(query, year=year, limit=limit)

    async def _search(self, query: str, *, year: int | None, limit: int) -> list[WorkRecord]:
        if not query.strip():
            return []
        params = {"search": query, "per-page": str(limit)}
        if year is not None:
            start, end = year_window(year)
            params["filter"] = f"publication_year:{start}-{end}"
        data = await self._get(f"{BASE_URL}/works", params=params)
        if data is None:
            return []
        results = as_dict(data).get("results")
        if not isinstance(results, list):
            raise bad_envelope(self.name)
        return parse_records(results, parse_work)

    async def _get(self, url: str, params: dict[str, str] | None = None) -> Any | None:
        return await get_json(
            self._http,
            url,
            source=self.name,
            timeout_s=self._timeout_s,
            params={**(params or {}), **self._base_params},
            headers=self._headers,
        )


def parse_work(item: dict[str, Any]) -> WorkRecord | None:
    """Một work OpenAlex; không có tiêu đề hoặc ID OpenAlex → None."""
    title = as_str(item.get("title")) or as_str(item.get("display_name"))
    openalex_id = _short_id(as_str(item.get("id")))
    if title is None or openalex_id is None:
        return None
    ids = as_dict(item.get("ids"))
    doi = normalize_doi(as_str(item.get("doi")) or as_str(ids.get("doi")))
    source = as_dict(as_dict(item.get("primary_location")).get("source"))
    field = as_dict(as_dict(item.get("primary_topic")).get("field"))
    return WorkRecord(
        source="openalex",
        source_id=openalex_id,
        title=title,
        authors=_authors(item.get("authorships")),
        year=as_year(item.get("publication_year")),
        venue=as_str(source.get("display_name")),
        doi=doi,
        arxiv_id=arxiv_id(ids.get("arxiv")) or arxiv_id(doi),
        openalex_id=openalex_id,
        abstract=_abstract(item.get("abstract_inverted_index")),
        language=as_str(item.get("language")),
        field=as_str(field.get("display_name")),
        work_type=as_str(item.get("type")),
        oa=_oa(item.get("best_oa_location")),
    )


def _short_id(value: str | None) -> str | None:
    if value is None:
        return None
    short = value.removeprefix(_ID_PREFIX)
    return short or None


def _authors(value: Any) -> list[WorkAuthor]:
    authors: list[WorkAuthor] = []
    for entry in as_list(value):
        data = as_dict(entry)
        name = as_dict(data.get("author")).get("display_name") or data.get("raw_author_name")
        author = author_from_name(name)
        if author is not None:
            authors.append(author)
    return authors


def _abstract(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    index: dict[str, list[int]] = {}
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            return None
        if not all(isinstance(p, int) and not isinstance(p, bool) for p in positions):
            return None
        index[word] = positions
    return abstract_from_inverted_index(index) or None


def _oa(value: Any) -> list[OaLocation]:
    location = as_dict(value)
    pdf_url = as_str(location.get("pdf_url"))
    if pdf_url is None:
        return []
    return [OaLocation(pdf_url=pdf_url, license=as_str(location.get("license")))]
