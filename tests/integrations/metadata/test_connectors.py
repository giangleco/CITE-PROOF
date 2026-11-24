"""Connector Crossref, OpenAlex, Semantic Scholar trên phản hồi thật đã ghi (T-301).

Fixture ở `tests/fixtures/metadata/` do `scripts/record_metadata_fixtures.py` ghi; bản ghi hỏng
được dựng bằng cách sửa bản sao của phản hồi thật ngay trong test.
"""

import re
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from citeproof.core.domain import ParsedReference
from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.integrations.metadata import (
    SOURCES,
    CrossrefConnector,
    MetadataConnector,
    OpenAlexConnector,
    SemanticScholarConnector,
    abstract_from_inverted_index,
    clean_jats,
    user_agent,
)
from tests.integrations.metadata.fakes import FakeApi, json_response, load_fixture, sequence

GENERIC_UA = "CiteProof/0.1 (+citation checker)"
EMAIL = "ops@example.org"


def _crossref(api: FakeApi, *, contact_email: str | None = None) -> CrossrefConnector:
    return CrossrefConnector(api.client(), contact_email=contact_email)


def _openalex(api: FakeApi, *, contact_email: str | None = None) -> OpenAlexConnector:
    return OpenAlexConnector(api.client(), contact_email=contact_email)


def _s2(api: FakeApi, *, api_key: str | None = None) -> SemanticScholarConnector:
    return SemanticScholarConnector(api.client(), api_key=api_key)


def _connector(source: str, api: FakeApi) -> MetadataConnector:
    if source == "crossref":
        return _crossref(api)
    if source == "openalex":
        return _openalex(api)
    return _s2(api)


def test_sources_order() -> None:
    assert SOURCES == ("crossref", "openalex", "semantic_scholar")


# --- Crossref -------------------------------------------------------------------------------


async def test_crossref_doi_record() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("crossref_doi_he")))
    record = await _crossref(api).get_by_doi("10.1109/CVPR.2016.90")

    assert record is not None
    assert record.source == "crossref"
    assert record.title == "Deep Residual Learning for Image Recognition"
    assert record.doi == "10.1109/cvpr.2016.90"
    assert record.source_id == "10.1109/cvpr.2016.90"
    assert record.authors[0].family == "He"
    assert record.authors[0].given == "Kaiming"
    assert [a.family for a in record.authors] == ["He", "Zhang", "Ren", "Sun"]
    assert record.year == 2016
    assert record.venue == "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)"
    assert record.work_type == "proceedings-article"
    assert record.oa == []
    (request,) = api.requests
    assert request.url.host == "api.crossref.org"
    assert unquote(request.url.path) == "/works/10.1109/CVPR.2016.90"


async def test_crossref_search_parses_items_and_cleans_abstract() -> None:
    fixture = load_fixture("crossref_search_hochreiter")
    api = FakeApi(lambda request: json_response(fixture))
    ref = ParsedReference(
        title="Long short-term memory",
        authors=["Hochreiter, S.", "Schmidhuber, J."],
        year=1997,
        venue="Neural Computation",
        label="[3]",
    )
    records = await _crossref(api).search(ref, limit=3)

    assert len(records) == len(fixture["message"]["items"])
    first = records[0]
    assert first.title == "Long Short-Term Memory"
    assert first.doi == "10.1162/neco.1997.9.8.1735"
    assert first.year == 1997
    assert first.authors[0].family == "Hochreiter"
    assert first.abstract is not None
    assert first.abstract.startswith("Learning to store information")
    assert "<" not in first.abstract
    (request,) = api.requests
    params = request.url.params
    assert unquote(request.url.path) == "/works"
    query = params["query.bibliographic"]
    assert "Long short-term memory" in query
    assert "Hochreiter" in query
    assert "1997" in query
    assert "[3]" not in query  # nhãn trong bài không phải dữ liệu thư mục
    assert params["rows"] == "3"
    assert "title" in params["select"].split(",")
    assert "filter" not in params


async def test_crossref_search_text_filters_year_window() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("crossref_search_hochreiter")))
    await _crossref(api).search_text("long short-term memory", year=1997, limit=2)
    params = api.requests[0].url.params
    assert params["query.bibliographic"] == "long short-term memory"
    assert params["rows"] == "2"
    assert params["filter"] == "from-pub-date:1996,until-pub-date:1998"


# --- OpenAlex -------------------------------------------------------------------------------


async def test_openalex_doi_record() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("openalex_doi_vaswani")))
    record = await _openalex(api).get_by_doi("10.48550/arxiv.1706.03762")

    assert record is not None
    assert record.source == "openalex"
    assert record.title == "Attention Is All You Need"
    assert record.openalex_id == "W2626778328"
    assert record.source_id == "W2626778328"
    assert record.doi == "10.48550/arxiv.1706.03762"
    assert record.arxiv_id == "1706.03762"
    assert record.year == 2017
    assert record.field == "Computer Science"
    assert record.language == "en"
    assert record.authors[0].family == "Vaswani"
    assert record.authors[0].given == "Ashish"
    assert record.abstract is not None
    assert record.abstract.startswith("The dominant sequence transduction models")
    # best_oa_location của bản ghi này không có pdf_url: không có lựa chọn OA.
    assert record.oa == []
    (request,) = api.requests
    assert request.url.host == "api.openalex.org"
    assert unquote(request.url.path) == "/works/doi:10.48550/arxiv.1706.03762"
    assert "best_oa_location" in request.url.params["select"].split(",")
    assert "mailto" not in request.url.params


async def test_openalex_field_and_oa() -> None:
    fixture = load_fixture("openalex_search_he")
    api = FakeApi(lambda request: json_response(fixture))
    records = await _openalex(api).search_text("Deep residual learning for image recognition")

    assert len(records) == len(fixture["results"])
    first = records[0]
    assert first.field == "Computer Science"
    expected_pdf = fixture["results"][0]["best_oa_location"]["pdf_url"]
    assert expected_pdf
    assert first.oa[0].pdf_url == expected_pdf
    assert first.arxiv_id == "1512.03385"
    params = api.requests[0].url.params
    assert params["search"] == "Deep residual learning for image recognition"
    assert params["per-page"] == "5"
    assert "filter" not in params


async def test_openalex_arxiv_id_without_version() -> None:
    fixture = load_fixture("openalex_doi_vaswani")
    fixture["ids"]["arxiv"] = "https://arxiv.org/abs/1706.03762v5"
    api = FakeApi(lambda request: json_response(fixture))
    record = await _openalex(api).get_by_doi("10.48550/arxiv.1706.03762")
    assert record is not None
    assert record.arxiv_id == "1706.03762"


async def test_openalex_search_text_filters_year_window() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("openalex_search_he")))
    await _openalex(api).search_text("residual learning", year=2016, limit=3)
    params = api.requests[0].url.params
    assert params["filter"] == "publication_year:2015-2017"
    assert params["per-page"] == "3"


# --- Semantic Scholar -----------------------------------------------------------------------


async def test_semantic_scholar_doi_record() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("semantic_scholar_doi_devlin")))
    record = await _s2(api).get_by_doi("10.18653/v1/N19-1423")

    assert record is not None
    assert record.source == "semantic_scholar"
    assert record.title.startswith("BERT: Pre-training of Deep Bidirectional Transformers")
    assert record.doi == "10.18653/v1/n19-1423"
    assert record.arxiv_id == "1810.04805"
    assert record.s2_id == record.source_id
    assert re.fullmatch(r"[0-9a-f]{40}", record.source_id)
    assert record.year == 2019
    assert record.authors[0].family == "Devlin"
    (request,) = api.requests
    assert request.url.host == "api.semanticscholar.org"
    assert unquote(request.url.path) == "/graph/v1/paper/DOI:10.18653/v1/N19-1423"
    assert "externalIds" in request.url.params["fields"].split(",")
    assert "x-api-key" not in request.headers


async def test_semantic_scholar_search() -> None:
    fixture = load_fixture("semantic_scholar_search_vaswani")
    api = FakeApi(lambda request: json_response(fixture))
    ref = ParsedReference(
        title="Attention is all you need", authors=["Vaswani, A."], year=2017, label="[1]"
    )
    records = await _s2(api, api_key="s2-secret").search(ref, limit=3)

    assert len(records) == len(fixture["data"])
    assert records[0].title.casefold() == "attention is all you need"
    assert records[0].year == 2017
    assert records[0].authors[0].family == "Vaswani"
    (request,) = api.requests
    assert unquote(request.url.path) == "/graph/v1/paper/search"
    assert request.url.params["query"] == "Attention is all you need"
    assert request.url.params["limit"] == "3"
    assert "year" not in request.url.params
    assert request.headers["x-api-key"] == "s2-secret"


async def test_semantic_scholar_query_drops_hyphens_and_filters_year() -> None:
    api = FakeApi(lambda request: json_response({"total": 0, "offset": 0}))
    records = await _s2(api).search_text("Long short-term memory", year=1997, limit=5)
    assert records == []  # không có "data" khi không có kết quả: nguồn vẫn tra thành công
    params = api.requests[0].url.params
    assert params["query"] == "Long short term memory"
    assert params["year"] == "1996-1998"


# --- Ánh xạ lỗi -----------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
async def test_crossref_404_is_not_found(source: str) -> None:
    # Crossref trả 404 "Resource not found." cho DOI arXiv (đăng ký ở DataCite, không ở Crossref).
    api = FakeApi(lambda request: httpx.Response(404, text="Resource not found."))
    assert await _connector(source, api).get_by_doi("10.48550/arxiv.1706.03762") is None


@pytest.mark.parametrize("source", SOURCES)
async def test_429_raises_retryable(source: str) -> None:
    body = {"message": "Too Many Requests.", "code": "429"}
    api = FakeApi(lambda request: json_response(body, 429, {"Retry-After": "7"}))
    with pytest.raises(RetryableError) as caught:
        await _connector(source, api).search_text("secret query words", year=None, limit=5)
    assert caught.value.retry_after == 7.0
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    "outcome",
    [httpx.Response(503), httpx.Response(500), httpx.ReadTimeout("slow"), httpx.ConnectError("x")],
)
async def test_server_errors_and_timeouts_are_retryable(
    source: str, outcome: httpx.Response | Exception
) -> None:
    api = FakeApi(sequence(outcome))
    with pytest.raises(RetryableError) as caught:
        await _connector(source, api).get_by_doi("10.1109/cvpr.2016.90")
    assert caught.value.retry_after is None


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_other_http_errors_are_not_retryable(source: str, status: int) -> None:
    api = FakeApi(lambda request: httpx.Response(status, text="bad secret query"))
    with pytest.raises(ExternalServiceError) as caught:
        await _connector(source, api).search_text("secret query", year=None, limit=5)
    assert not isinstance(caught.value, RetryableError)
    assert "secret" not in str(caught.value)
    assert str(status) in str(caught.value)


@pytest.mark.parametrize("source", SOURCES)
async def test_invalid_json_is_external_error(source: str) -> None:
    api = FakeApi(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(ExternalServiceError):
        await _connector(source, api).search_text("query", year=None, limit=5)


async def test_retry_after_http_date_is_parsed() -> None:
    api = FakeApi(
        lambda request: httpx.Response(
            429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
        )
    )
    with pytest.raises(RetryableError) as caught:
        await _crossref(api).get_by_doi("10.1109/cvpr.2016.90")
    assert caught.value.retry_after == 0.0  # ngày đã qua: thử lại ngay


# --- Bản ghi thiếu hoặc lạ trường (Review Focus #5) ------------------------------------------


async def test_malformed_records_skipped_not_errors() -> None:
    crossref = load_fixture("crossref_search_hochreiter")
    good = crossref["message"]["items"][0]
    crossref["message"]["items"] = [
        {**good, "title": []},
        {**good, "DOI": None},
        None,
        "not a record",
        {
            **good,
            "author": None,
            "issued": {"date-parts": [[None]]},
            "published": None,
            "container-title": None,
        },
        {**good, "issued": None, "author": [{"name": "LSTM Group"}, {"given": "Only"}]},
    ]
    records = await _crossref(FakeApi(lambda r: json_response(crossref))).search_text(
        "q", year=None, limit=5
    )
    assert len(records) == 2
    assert records[0].authors == []
    assert records[0].year is None
    assert records[0].venue is None
    assert records[1].year == 1997  # `issued` thiếu: lấy năm từ `published`
    assert [(a.family, a.given) for a in records[1].authors] == [("LSTM Group", None)]

    openalex = load_fixture("openalex_search_he")
    first = openalex["results"][0]
    openalex["results"] = [
        {**first, "publication_year": None, "primary_topic": None, "best_oa_location": None},
        {**first, "title": None, "display_name": ""},
        {**first, "authorships": None, "ids": None, "abstract_inverted_index": "odd"},
    ]
    records = await _openalex(FakeApi(lambda r: json_response(openalex))).search_text("q")
    assert len(records) == 2
    assert records[0].year is None
    assert records[0].field is None
    assert records[0].oa == []
    assert records[1].authors == []
    assert records[1].abstract is None

    s2 = load_fixture("semantic_scholar_search_vaswani")
    paper = s2["data"][0]
    s2["data"] = [
        {**paper, "authors": None, "externalIds": None, "year": "2017"},
        {**paper, "title": None},
        {**paper, "authors": [{"authorId": None, "name": None}, {"name": "Noam Shazeer"}]},
    ]
    records = await _s2(FakeApi(lambda r: json_response(s2))).search_text("q")
    assert len(records) == 2
    assert records[0].authors == []
    assert records[0].doi is None
    assert records[0].year is None
    assert [a.family for a in records[1].authors] == ["Shazeer"]


@pytest.mark.parametrize(
    ("source", "body"),
    [
        ("crossref", {"status": "ok", "message": {"DOI": "10.1000/x", "title": []}}),
        ("openalex", {"id": "https://openalex.org/W1", "title": None}),
        ("semantic_scholar", {"paperId": "abc", "title": ""}),
    ],
)
async def test_doi_record_without_title_is_none(source: str, body: dict[str, Any]) -> None:
    api = FakeApi(lambda request: json_response(body))
    assert await _connector(source, api).get_by_doi("10.1000/x") is None


@pytest.mark.parametrize(
    ("source", "body"),
    [
        ("crossref", {"status": "ok", "message": {"items": "oops"}}),
        ("openalex", {"meta": {}, "results": None}),
        ("semantic_scholar", ["not", "an", "object"]),
    ],
)
async def test_broken_envelope_is_external_error(source: str, body: Any) -> None:
    api = FakeApi(lambda request: json_response(body))
    with pytest.raises(ExternalServiceError):
        await _connector(source, api).search_text("q", year=None, limit=5)


# --- User-Agent và polite pool --------------------------------------------------------------


def test_user_agent_generic_without_email() -> None:
    assert user_agent(None) == GENERIC_UA
    assert user_agent("") == GENERIC_UA
    assert "@" not in user_agent(None)
    assert user_agent(EMAIL) == f"{GENERIC_UA}; mailto:{EMAIL}"


@pytest.mark.parametrize("source", SOURCES)
async def test_requests_carry_no_email_by_default(source: str) -> None:
    api = FakeApi(lambda request: httpx.Response(404))
    await _connector(source, api).get_by_doi("10.1000/x")
    (request,) = api.requests
    assert request.headers["User-Agent"] == GENERIC_UA
    assert "@" not in str(request.url)
    assert "mailto" not in request.url.params


async def test_contact_email_polite_pool() -> None:
    api = FakeApi(lambda request: httpx.Response(404))
    await _crossref(api, contact_email=EMAIL).get_by_doi("10.1000/x")
    await _openalex(api, contact_email=EMAIL).get_by_doi("10.1000/x")
    crossref, openalex = api.requests
    assert crossref.headers["User-Agent"].endswith(f"; mailto:{EMAIL}")
    assert openalex.url.params["mailto"] == EMAIL
    assert openalex.headers["User-Agent"].endswith(f"; mailto:{EMAIL}")


async def test_openalex_api_key_sent_only_when_set() -> None:
    api = FakeApi(lambda request: json_response(load_fixture("openalex_search_he")))
    await OpenAlexConnector(api.client(), contact_email=None, api_key="oa-secret").search_text("q")
    await _openalex(api).search_text("q")
    with_key, without_key = api.requests
    assert with_key.url.params["api_key"] == "oa-secret"
    assert "api_key" not in without_key.url.params


@pytest.mark.parametrize(
    "outcome", [httpx.Response(400), httpx.Response(429), httpx.ReadTimeout("slow")]
)
async def test_openalex_api_key_not_in_errors(outcome: httpx.Response | Exception) -> None:
    api = FakeApi(sequence(outcome))
    connector = OpenAlexConnector(api.client(), contact_email=None, api_key="oa-secret")
    with pytest.raises(ExternalServiceError) as caught:
        await connector.get_by_doi("10.1109/cvpr.2016.90")
    assert "oa-secret" not in str(caught.value)
    assert "oa-secret" not in repr(caught.value)


async def test_timeout_is_per_request() -> None:
    api = FakeApi(lambda request: httpx.Response(404))
    connector = CrossrefConnector(api.client(), contact_email=None, timeout_s=4.5)
    await connector.get_by_doi("10.1000/x")
    assert api.requests[0].extensions["timeout"]["read"] == 4.5


# --- Abstract ---------------------------------------------------------------------------------


def test_clean_jats_strips_tags_and_leading_title() -> None:
    text = (
        "<jats:title>Abstract</jats:title>\n  <jats:p>Water is H<jats:sub>2</jats:sub>O &amp; "
        "more   text.</jats:p><jats:p>Second <jats:italic>para</jats:italic>.</jats:p>"
    )
    assert clean_jats(text) == "Water is H2O & more text.\n\nSecond para."
    assert clean_jats("Plain   abstract\ntext") == "Plain abstract text"
    assert clean_jats("<jats:p> </jats:p>") == ""


def test_abstract_from_inverted_index_orders_words() -> None:
    index = {"Attention": [0], "is": [1, 4], "all": [2], "you": [3], "need": [5]}
    assert abstract_from_inverted_index(index) == "Attention is all you is need"
    assert abstract_from_inverted_index({}) == ""
    # Vị trí lớn hoặc có khoảng trống không làm cấp phát mảng theo vị trí lớn nhất.
    assert abstract_from_inverted_index({"b": [10**9], "a": [3]}) == "a b"
