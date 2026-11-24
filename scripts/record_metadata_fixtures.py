"""Ghi phản hồi JSON thật của Crossref, OpenAlex, Semantic Scholar cho test metadata (T-301).

    uv run python scripts/record_metadata_fixtures.py [--dir tests/fixtures/metadata]

Chỉ chạy tay, CI không chạy. Gọi qua chính các connector của `citeproof.integrations.metadata`
(cùng URL, tham số `select`/`fields`) với User-Agent chung, không email, không khóa API. Truy
vấn chỉ là dữ liệu thư mục công khai của các bài nổi tiếng trong bộ golden (Vaswani 2017,
Devlin 2019, He 2016, Hochreiter 1997). Mỗi fixture chỉ là thân phản hồi JSON, đã cắt các
trường connector không đọc (danh sách tài liệu tham khảo, cơ quan của tác giả…) cho gọn.

Lưu ý khi ghi lại: Crossref không có DOI arXiv `10.48550/…` (đăng ký ở DataCite) nên trả 404;
vì vậy bản ghi Crossref theo DOI dùng bài của He et al. (CVPR 2016).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from citeproof.core.domain import ParsedReference
from citeproof.core.errors import RetryableError
from citeproof.integrations.metadata import (
    CrossrefConnector,
    MetadataConnector,
    OpenAlexConnector,
    SemanticScholarConnector,
)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "metadata"
ATTEMPTS = 6
WAIT_S = 10.0  # Semantic Scholar không khóa API hay trả 429

Connectors = Mapping[str, MetadataConnector]
Call = Callable[[Connectors], Awaitable[object]]

HOCHREITER = ParsedReference(
    title="Long short-term memory",
    authors=["Hochreiter, S.", "Schmidhuber, J."],
    year=1997,
    venue="Neural Computation",
)

FIXTURES: dict[str, Call] = {
    "crossref_doi_he": lambda c: c["crossref"].get_by_doi("10.1109/CVPR.2016.90"),
    "crossref_search_hochreiter": lambda c: c["crossref"].search(HOCHREITER, limit=3),
    "openalex_doi_vaswani": lambda c: c["openalex"].get_by_doi("10.48550/arXiv.1706.03762"),
    "openalex_search_he": lambda c: c["openalex"].search_text(
        "Deep residual learning for image recognition", limit=3
    ),
    "semantic_scholar_doi_devlin": lambda c: c["semantic_scholar"].get_by_doi(
        "10.18653/v1/N19-1423"
    ),
    "semantic_scholar_search_vaswani": lambda c: c["semantic_scholar"].search_text(
        "Attention is all you need", limit=3
    ),
}

_CROSSREF_KEYS = {
    "DOI",
    "title",
    "subtitle",
    "author",
    "issued",
    "published",
    "published-print",
    "published-online",
    "container-title",
    "type",
    "ISBN",
    "abstract",
    "language",
}
_CROSSREF_AUTHOR_KEYS = {"given", "family", "name", "sequence"}
_OA_LOCATION_KEYS = {"is_oa", "landing_page_url", "pdf_url", "license"}


def _pick(data: Any, keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k in keys} if isinstance(data, dict) else data


def _trim_crossref_work(work: dict[str, Any]) -> dict[str, Any]:
    work = _pick(work, _CROSSREF_KEYS)
    if isinstance(work.get("author"), list):
        work["author"] = [_pick(a, _CROSSREF_AUTHOR_KEYS) for a in work["author"]]
    return work


def _trim_openalex_location(location: Any) -> Any:
    if not isinstance(location, dict):
        return location
    trimmed = _pick(location, _OA_LOCATION_KEYS)
    source = location.get("source")
    trimmed["source"] = _pick(source, {"display_name"}) if isinstance(source, dict) else source
    return trimmed


def _trim_openalex_work(work: dict[str, Any]) -> dict[str, Any]:
    work = dict(work)
    work["authorships"] = [
        {
            "author_position": a.get("author_position"),
            "author": _pick(a.get("author"), {"display_name"}),
            "raw_author_name": a.get("raw_author_name"),
        }
        for a in work.get("authorships") or []
    ]
    for key in ("primary_location", "best_oa_location"):
        work[key] = _trim_openalex_location(work.get(key))
    topic = work.get("primary_topic")
    if isinstance(topic, dict):
        work["primary_topic"] = {
            "display_name": topic.get("display_name"),
            "field": _pick(topic.get("field"), {"display_name"}),
        }
    return work


def trim(name: str, body: Any) -> Any:
    """Bỏ trường connector không đọc; giữ nguyên giá trị các trường còn lại."""
    if name.startswith("crossref_doi"):
        return {
            **_pick(body, {"status", "message-type"}),
            "message": _trim_crossref_work(body["message"]),
        }
    if name.startswith("crossref_search"):
        message = body["message"]
        return {
            **_pick(body, {"status", "message-type"}),
            "message": {
                "total-results": message.get("total-results"),
                "items": [_trim_crossref_work(item) for item in message["items"]],
            },
        }
    if name.startswith("openalex_doi"):
        return _trim_openalex_work(body)
    if name.startswith("openalex_search"):
        return {
            "meta": _pick(body.get("meta"), {"count", "per_page"}),
            "results": [_trim_openalex_work(work) for work in body["results"]],
        }
    if name.startswith("semantic_scholar_doi"):
        return _pick(body, set(body) - {"openAccessPdf"})
    if name.startswith("semantic_scholar_search"):
        return {
            **_pick(body, {"total", "offset", "next"}),
            "data": [_pick(p, set(p) - {"openAccessPdf"}) for p in body.get("data", [])],
        }
    raise SystemExit(f"Không biết cách cắt fixture {name}")


async def record(name: str, call: Call, connectors: Connectors, captured: list[Any]) -> Any:
    for attempt in range(1, ATTEMPTS + 1):
        captured.clear()
        try:
            await call(connectors)
        except RetryableError:
            print(f"{name}: tạm thời không khả dụng, thử lại lần {attempt}", file=sys.stderr)
            await asyncio.sleep(WAIT_S * attempt)
            continue
        if not captured:
            raise SystemExit(f"{name}: không nhận được phản hồi JSON (HTTP khác 200?)")
        return captured[-1]
    raise SystemExit(f"{name}: hết số lần thử")


async def fetch_all() -> dict[str, Any]:
    """Thân phản hồi đã cắt của mọi fixture, theo tên tệp."""
    captured: list[Any] = []

    async def keep_json(response: httpx.Response) -> None:
        await response.aread()
        if response.status_code == httpx.codes.OK:
            captured.append(response.json())

    bodies: dict[str, Any] = {}
    async with httpx.AsyncClient(event_hooks={"response": [keep_json]}) as http:
        connectors: dict[str, MetadataConnector] = {
            "crossref": CrossrefConnector(http, contact_email=None, timeout_s=60.0),
            "openalex": OpenAlexConnector(http, contact_email=None, timeout_s=60.0),
            "semantic_scholar": SemanticScholarConnector(http, api_key=None, timeout_s=60.0),
        }
        for name, call in FIXTURES.items():
            bodies[name] = trim(name, await record(name, call, connectors, captured))
            await asyncio.sleep(3.5)  # dưới 0,3 yêu cầu/giây cho Semantic Scholar không khóa
    return bodies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    bodies = asyncio.run(fetch_all())
    args.dir.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        path = args.dir / f"{name}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{path.name}: {path.stat().st_size} byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
