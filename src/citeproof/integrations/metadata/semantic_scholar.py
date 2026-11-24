"""Connector Semantic Scholar Graph API (spec §7.3).

`/graph/v1/paper/DOI:{doi}` tra theo DOI; `/graph/v1/paper/search` tìm theo từ khóa. Khóa
API (tùy chọn) gửi trong header `x-api-key`; không có khóa thì giới hạn tốc độ chung rất
thấp (mặc định 0,3 yêu cầu/giây). Không nhận email liên hệ.
"""

from typing import Any

import httpx

from citeproof.core.domain import ParsedReference, normalize_doi
from citeproof.integrations.metadata.base import (
    DEFAULT_TIMEOUT_S,
    arxiv_id,
    as_dict,
    as_list,
    as_str,
    as_year,
    author_from_name,
    bad_envelope,
    collapse,
    doi_path,
    get_json,
    parse_record,
    parse_records,
    search_fields,
    title_query,
    user_agent,
    year_window,
)
from citeproof.integrations.metadata.models import WorkAuthor, WorkRecord

BASE_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,externalIds,title,authors,year,venue,abstract,publicationTypes"


class SemanticScholarConnector:
    name = "semantic_scholar"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_key: str | None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._headers = {"User-Agent": user_agent(None)}
        if api_key:
            self._headers["x-api-key"] = api_key
        self._timeout_s = timeout_s

    async def get_by_doi(self, doi: str) -> WorkRecord | None:
        data = await self._get(f"{BASE_URL}/paper/DOI:{doi_path(doi)}", {"fields": FIELDS})
        if data is None:
            return None
        if not isinstance(data, dict):
            raise bad_envelope(self.name)
        return parse_record(data, parse_paper)

    async def search(self, ref: ParsedReference, limit: int = 5) -> list[WorkRecord]:
        return await self._search(title_query(search_fields(ref)), year=None, limit=limit)

    async def search_text(
        self, query: str, *, year: int | None = None, limit: int = 5
    ) -> list[WorkRecord]:
        return await self._search(query, year=year, limit=limit)

    async def _search(self, query: str, *, year: int | None, limit: int) -> list[WorkRecord]:
        # API không có cú pháp truy vấn; từ có gạch nối không khớp gì nên đổi gạch thành cách.
        text = collapse(query.replace("-", " "))
        if not text:
            return []
        params = {"query": text, "limit": str(limit), "fields": FIELDS}
        if year is not None:
            start, end = year_window(year)
            params["year"] = f"{start}-{end}"
        data = await self._get(f"{BASE_URL}/paper/search", params)
        if data is None:
            return []
        if not isinstance(data, dict):
            raise bad_envelope(self.name)
        # Không có kết quả: API bỏ hẳn khóa `data`.
        papers = data.get("data", [])
        if not isinstance(papers, list):
            raise bad_envelope(self.name)
        return parse_records(papers, parse_paper)

    async def _get(self, url: str, params: dict[str, str]) -> Any | None:
        return await get_json(
            self._http,
            url,
            source=self.name,
            timeout_s=self._timeout_s,
            params=params,
            headers=self._headers,
        )


def parse_paper(item: dict[str, Any]) -> WorkRecord | None:
    """Một bài Semantic Scholar; không có tiêu đề hoặc paperId → None."""
    title = as_str(item.get("title"))
    paper_id = as_str(item.get("paperId"))
    if title is None or paper_id is None:
        return None
    ids = as_dict(item.get("externalIds"))
    doi = normalize_doi(as_str(ids.get("DOI")))
    types = [t for t in (as_str(v) for v in as_list(item.get("publicationTypes"))) if t]
    return WorkRecord(
        source="semantic_scholar",
        source_id=paper_id,
        title=title,
        authors=_authors(item.get("authors")),
        year=as_year(item.get("year")),
        venue=as_str(item.get("venue")),
        doi=doi,
        arxiv_id=arxiv_id(ids.get("ArXiv")) or arxiv_id(doi),
        s2_id=paper_id,
        abstract=as_str(item.get("abstract")),
        work_type=types[0] if types else None,
    )


def _authors(value: Any) -> list[WorkAuthor]:
    authors: list[WorkAuthor] = []
    for entry in as_list(value):
        author = author_from_name(as_dict(entry).get("name"))
        if author is not None:
            authors.append(author)
    return authors
