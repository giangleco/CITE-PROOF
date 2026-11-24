"""Fixture bản mở (`tests/fixtures/oa/`) và gateway OpenAlex giả cho test S5.

JSON OpenAlex và Unpaywall viết tay theo đúng dạng phản hồi thật (môi trường dựng fixture
không có mạng, preflight F40); `open_paper.pdf` sinh bằng reportlab (một trang, có abstract).
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from citeproof.integrations.metadata import (
    InMemoryMetadataCache,
    MetadataGateway,
    OpenAlexConnector,
    RateLimiter,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "oa"
OPEN_PDF = (FIXTURES / "open_paper.pdf").read_bytes()
JUMPER_DOI = "10.1038/s41586-021-03819-2"
JUMPER_PDF = "https://www.nature.com/articles/s41586-021-03819-2.pdf"


def load_oa(name: str) -> Any:
    """Bản sao mới của một fixture JSON trong `tests/fixtures/oa/`."""
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def pdf_stream(content: bytes = OPEN_PDF, status: int = 200) -> httpx.Response:
    """Response chưa đọc (như từ transport thật) để `SafeHttpClient` đọc dần."""
    return httpx.Response(
        status, headers={"content-type": "application/pdf"}, stream=httpx.ByteStream(content)
    )


def html_stream(status: int = 200) -> httpx.Response:
    body = b"<!doctype html><html><body>Landing page</body></html>"
    return httpx.Response(
        status, headers={"content-type": "text/html"}, stream=httpx.ByteStream(body)
    )


async def _no_sleep(seconds: float) -> None:
    return None


def openalex_gateway_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], MetadataGateway]:
    """Factory gateway chỉ có OpenAlex, cache trong bộ nhớ, không chờ."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def factory() -> MetadataGateway:
        return MetadataGateway(
            {"openalex": OpenAlexConnector(http, contact_email=None)},
            InMemoryMetadataCache(),
            {"openalex": RateLimiter(1000.0, sleep=_no_sleep)},
            breaker_threshold=3,
            sleep=_no_sleep,
        )

    return factory
