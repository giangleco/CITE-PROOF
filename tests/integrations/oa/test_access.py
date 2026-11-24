"""Xác định bản mở cho một work: arXiv, ACL Anthology, OpenAlex, Unpaywall (spec §7.5, QĐ-07).

Không gọi mạng: OpenAlex qua gateway có transport giả, Unpaywall qua `httpx.MockTransport`.
"""

import uuid
from urllib.parse import parse_qs

import httpx
import pytest

from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.integrations.metadata import MetadataGateway, user_agent
from citeproof.integrations.oa import (
    AccessOption,
    AclAccess,
    ArxivAccess,
    OpenAlexAccess,
    UnpaywallAccess,
    WorkRef,
    make_resolvers,
)
from tests.integrations.metadata.fakes import FakeApi, json_response
from tests.integrations.oa.fakes import (
    JUMPER_DOI,
    JUMPER_PDF,
    load_oa,
    openalex_gateway_factory,
)


def _work(doi: str | None = None, arxiv_id: str | None = None) -> WorkRef:
    return WorkRef(work_id=uuid.uuid4(), doi=doi, arxiv_id=arxiv_id)


# --- arXiv ------------------------------------------------------------------------------------


async def test_arxiv_option() -> None:
    options = await ArxivAccess().resolve(_work(arxiv_id="1706.03762"))
    assert options == [
        AccessOption(url="https://export.arxiv.org/pdf/1706.03762", license=None, provider="arxiv")
    ]


async def test_arxiv_option_from_arxiv_doi_and_old_style_id() -> None:
    [from_doi] = await ArxivAccess().resolve(_work(doi="10.48550/arxiv.1706.03762"))
    assert from_doi.url == "https://export.arxiv.org/pdf/1706.03762"
    [old] = await ArxivAccess().resolve(_work(arxiv_id="cs/0112017"))
    assert old.url == "https://export.arxiv.org/pdf/cs/0112017"


@pytest.mark.parametrize("arxiv_id", [None, "", "../../etc/passwd", "1706.03762?x=1"])
async def test_arxiv_without_valid_id_has_no_option(arxiv_id: str | None) -> None:
    assert await ArxivAccess().resolve(_work(doi="10.1000/x", arxiv_id=arxiv_id)) == []


# --- ACL Anthology ----------------------------------------------------------------------------


async def test_acl_option_from_doi() -> None:
    [old] = await AclAccess().resolve(_work(doi="10.18653/v1/n19-1423"))
    assert old == AccessOption(
        url="https://aclanthology.org/N19-1423.pdf", license="CC BY 4.0", provider="acl"
    )
    [new] = await AclAccess().resolve(_work(doi="10.18653/v1/2020.acl-main.1"))
    assert new.url == "https://aclanthology.org/2020.acl-main.1.pdf"
    assert new.license == "CC BY 4.0"
    [upper] = await AclAccess().resolve(_work(doi="10.18653/V1/N19-1423"))
    assert upper.url == "https://aclanthology.org/N19-1423.pdf"


@pytest.mark.parametrize(
    "doi", [None, "10.1038/s41586-021-03819-2", "10.18653/v1/", "10.18653/v1/a/../b"]
)
async def test_acl_other_dois_have_no_option(doi: str | None) -> None:
    assert await AclAccess().resolve(_work(doi=doi)) == []


# --- OpenAlex ---------------------------------------------------------------------------------


async def test_openalex_option_from_best_oa_location() -> None:
    api = FakeApi(lambda request: json_response(load_oa("openalex_doi_jumper")))
    access = OpenAlexAccess(openalex_gateway_factory(api))
    options = await access.resolve(_work(doi=JUMPER_DOI))
    assert options == [AccessOption(url=JUMPER_PDF, license="cc-by", provider="openalex")]
    [request] = api.requests
    assert request.url.host == "api.openalex.org"
    assert request.url.path == f"/works/doi:{JUMPER_DOI}"


async def test_openalex_without_pdf_or_doi_or_on_error_has_no_option() -> None:
    record = load_oa("openalex_doi_jumper")
    record["best_oa_location"]["pdf_url"] = None
    api = FakeApi(lambda request: json_response(record))
    assert await OpenAlexAccess(openalex_gateway_factory(api)).resolve(_work(JUMPER_DOI)) == []
    assert await OpenAlexAccess(openalex_gateway_factory(api)).resolve(_work()) == []
    failing = FakeApi(lambda request: httpx.Response(400))
    access = OpenAlexAccess(openalex_gateway_factory(failing))
    assert await access.resolve(_work(JUMPER_DOI)) == []


async def test_openalex_access_uses_one_gateway() -> None:
    calls = 0
    api = FakeApi(lambda request: json_response(load_oa("openalex_doi_jumper")))
    factory = openalex_gateway_factory(api)

    def counting_factory() -> MetadataGateway:
        nonlocal calls
        calls += 1
        return factory()

    access = OpenAlexAccess(counting_factory)
    await access.resolve(_work(JUMPER_DOI))
    await access.resolve(_work(JUMPER_DOI))
    assert calls == 1  # một gateway (một bộ ngắt mạch) cho cả lần chạy
    assert len(api.requests) == 1  # lần thứ hai trúng cache của gateway


# --- Unpaywall --------------------------------------------------------------------------------


async def test_unpaywall_option_from_best_oa_location() -> None:
    api = FakeApi(lambda request: json_response(load_oa("unpaywall_doi_jumper")))
    access = UnpaywallAccess(api.client(), "ops@citeproof.vn")
    options = await access.resolve(_work(doi=JUMPER_DOI))
    assert options == [AccessOption(url=JUMPER_PDF, license="cc-by", provider="unpaywall")]
    [request] = api.requests
    assert request.url.host == "api.unpaywall.org"
    assert request.url.path == f"/v2/{JUMPER_DOI}"
    assert parse_qs(request.url.query.decode()) == {"email": ["ops@citeproof.vn"]}
    assert request.headers["user-agent"] == user_agent("ops@citeproof.vn")


async def test_unpaywall_not_found_or_without_pdf() -> None:
    missing = FakeApi(lambda request: httpx.Response(404))
    assert await UnpaywallAccess(missing.client(), "a@b.vn").resolve(_work(JUMPER_DOI)) == []
    data = load_oa("unpaywall_doi_jumper")
    data["best_oa_location"] = None
    closed = FakeApi(lambda request: json_response(data))
    assert await UnpaywallAccess(closed.client(), "a@b.vn").resolve(_work(JUMPER_DOI)) == []
    assert await UnpaywallAccess(closed.client(), "a@b.vn").resolve(_work()) == []
    assert len(closed.requests) == 1  # không DOI thì không gọi API


async def test_unpaywall_errors_raise_external_service_error() -> None:
    unavailable = FakeApi(lambda request: httpx.Response(503))
    with pytest.raises(RetryableError) as info:
        await UnpaywallAccess(unavailable.client(), "a@b.vn").resolve(_work(JUMPER_DOI))
    assert "a@b.vn" not in str(info.value)
    broken = FakeApi(lambda request: httpx.Response(200, text="not json"))
    with pytest.raises(ExternalServiceError):
        await UnpaywallAccess(broken.client(), "a@b.vn").resolve(_work(JUMPER_DOI))


# --- Thứ tự -----------------------------------------------------------------------------------


async def test_unpaywall_skipped_without_email() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    factory = openalex_gateway_factory(lambda request: httpx.Response(404))
    without = make_resolvers(http=http, gateway_factory=factory, contact_email=None)
    assert [type(r) for r in without] == [ArxivAccess, AclAccess, OpenAlexAccess]
    blank = make_resolvers(http=http, gateway_factory=factory, contact_email="  ")
    assert [type(r) for r in blank] == [ArxivAccess, AclAccess, OpenAlexAccess]
    with_email = make_resolvers(http=http, gateway_factory=factory, contact_email="ops@b.vn")
    assert [type(r) for r in with_email] == [
        ArxivAccess,
        AclAccess,
        OpenAlexAccess,
        UnpaywallAccess,
    ]
    await http.aclose()
