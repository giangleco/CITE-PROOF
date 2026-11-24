"""GrobidClient: tham số form, ánh xạ lỗi; so cấu trúc TEI thật với bản ghi (marker `grobid`)."""

import os
import re
from collections.abc import Callable
from urllib.parse import parse_qsl

import httpx
import pytest

from citeproof.core.errors import ParseError, RetryableError
from citeproof.integrations.grobid import GrobidClient
from citeproof.parsing.tei import parse_fulltext_tei
from tests.golden.cases import GOLDEN_DIR

BASE = "http://grobid.test:8070"
TEI_OK = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body/></text></TEI>'
_PART = re.compile(
    rb'name="([^"]+)"(?:; filename="[^"]*")?\r\n(?:Content-Type: [^\r\n]*\r\n)?\r\n(.*?)\r\n--',
    re.DOTALL,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, *, timeout_s: float = 300.0) -> GrobidClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GrobidClient(BASE + "/", http, timeout_s=timeout_s)


def _multipart(request: httpx.Request) -> list[tuple[str, bytes]]:
    return [(name.decode(), value) for name, value in _PART.findall(request.content)]


async def test_process_fulltext_sends_form_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=TEI_OK)

    assert await _client(handler, timeout_s=42.0).process_fulltext(b"%PDF-1.7 fake") == TEI_OK
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/api/processFulltextDocument"
    assert request.headers["Accept"] == "application/xml"
    assert request.extensions["timeout"]["read"] == 42.0
    fields = _multipart(request)
    assert ("input", b"%PDF-1.7 fake") in fields
    values = [(name, value.decode()) for name, value in fields if name != "input"]
    assert sorted(values) == sorted(
        [
            ("segmentSentences", "1"),
            ("includeRawCitations", "1"),
            ("consolidateHeader", "0"),
            ("consolidateCitations", "0"),
            *[("teiCoordinates", v) for v in ("p", "s", "head", "ref", "biblStruct", "figure")],
        ]
    )


async def test_process_citations_sends_each_citation() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=TEI_OK)

    raws = ["Nguyễn Văn An (2020), Phân loại văn bản.", "K. He et al., 2016."]
    assert await _client(handler).process_citations(raws) == TEI_OK
    (request,) = seen
    assert str(request.url) == f"{BASE}/api/processCitationList"
    form = parse_qsl(request.content.decode())
    assert [v for k, v in form if k == "citations"] == raws
    assert ("includeRawCitations", "1") in form
    assert ("consolidateCitations", "0") in form


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow")],
)
async def test_unreachable_grobid_is_retryable(error: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(RetryableError) as caught:
        await _client(handler).process_fulltext(b"%PDF-")
    assert caught.value.code == "GROBID_UNAVAILABLE"


async def test_busy_grobid_is_retryable() -> None:
    client = _client(lambda request: httpx.Response(503))
    with pytest.raises(RetryableError) as caught:
        await client.process_citations(["x"])
    assert caught.value.code == "GROBID_UNAVAILABLE"


@pytest.mark.parametrize("status", [500, 400, 204])
async def test_processing_error_is_parse_error(status: int) -> None:
    client = _client(lambda request: httpx.Response(status, text="[NO_BLOCKS] secret"))
    with pytest.raises(ParseError) as caught:
        await client.process_fulltext(b"%PDF-")
    assert caught.value.code == "GROBID_FAILED"
    assert not isinstance(caught.value, RetryableError)
    assert "secret" not in str(caught.value)  # không chép nội dung phản hồi vào thông điệp


def _structure(tei: str) -> tuple[list[str], int]:
    doc = parse_fulltext_tei(tei, page_count=None)
    heads = [p.text for p in doc.paragraphs if p.kind == "heading"]
    return heads, len(doc.references_raw or [])


@pytest.mark.grobid
@pytest.mark.skipif(
    os.environ.get("CITEPROOF_LIVE_GROBID") != "1", reason="cần CITEPROOF_LIVE_GROBID=1"
)
async def test_live_grobid_structure_matches_recorded() -> None:
    url = os.environ.get("CITEPROOF_GROBID_URL", "http://127.0.0.1:8070")
    async with httpx.AsyncClient() as http:
        client = GrobidClient(url, http, timeout_s=300.0)
        for name in ("en_ieee", "vi_mixed_apa", "vi_scanned_pages"):
            live = await client.process_fulltext((GOLDEN_DIR / f"{name}.pdf").read_bytes())
            recorded = (GOLDEN_DIR / f"{name}.fulltext.tei.xml").read_text(encoding="utf-8")
            assert _structure(live) == _structure(recorded), name
