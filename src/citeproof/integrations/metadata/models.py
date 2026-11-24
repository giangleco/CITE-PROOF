"""Bản ghi metadata chung cho mọi nguồn (spec §7.3, `09` §4.6).

`WorkRecord` thay cho `WorkCandidate` của `09`: kết quả tìm kiếm và kết quả tra theo DOI có
cùng dạng. Mọi chuỗi ở dạng NFC; DOI chữ thường qua `normalize_doi`; arXiv ID không có
hậu tố phiên bản.
"""

from pydantic import BaseModel, Field


class WorkAuthor(BaseModel):
    family: str
    given: str | None = None


class OaLocation(BaseModel):
    """Bản PDF mở do nguồn metadata báo (OpenAlex `best_oa_location`); chỉ tải qua `http_safe`."""

    pdf_url: str
    license: str | None = None


class WorkRecord(BaseModel):
    source: str  # tên nguồn trong `SOURCES`
    source_id: str  # DOI (Crossref), ID OpenAlex "W…", paperId (Semantic Scholar)
    title: str
    authors: list[WorkAuthor] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    isbn: str | None = None
    abstract: str | None = None
    language: str | None = None
    # Lĩnh vực OpenAlex (`primary_topic.field.display_name`), dùng cho cảnh báo ngoài lĩnh vực.
    field: str | None = None
    work_type: str | None = None
    oa: list[OaLocation] = Field(default_factory=list)
