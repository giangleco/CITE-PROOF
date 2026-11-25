"""Công cụ của A1 Reference Resolver (`07a` §4.6.2, §4.6.4; T-408, ADR-010).

`meta_search` tìm một chuỗi tự do qua mọi nguồn của gateway (Crossref, OpenAlex, Semantic
Scholar; không có arXiv), gộp kết quả theo DOI hoặc arXiv id và gán ID ngắn `cand_N` theo phiên
(`MetaSession`, thứ tự gặp lần đầu). Chỉ ứng viên thực sự hiện trong kết quả mới có ID: ứng viên
bị cắt vì `limit` hay vì giới hạn kích thước không nộp được. `meta_get` trả bản ghi đầy đủ của
một ứng viên trong phiên. `submit_resolution` kiểm tra ứng viên phải có trong phiên.

Mỗi kết quả tối đa khoảng 800 token (`MAX_RESULT_CHARS` ký tự) và bọc trong data block không
tin cậy, vì mọi chuỗi đến từ metadata bên thứ ba. Phiên ghi các nguồn đã tra thành công và các
nguồn lỗi để kết quả cuối của S3 liệt kê đúng `sources_searched`/`sources_failed`.
"""

import asyncio
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from citeproof.integrations.metadata import SOURCES, LookupOutcome, MetadataGateway, WorkRecord
from citeproof.llm.context import render_data_block
from citeproof.llm.tools import SubmitTool, Tool, ToolError, ToolResult
from citeproof.llm.types import OutputModel
from citeproof.modules.references.normalize import normalize_doi

# Khoảng 800 token (~4 ký tự/token) cho một kết quả công cụ (`07a` §4.5.2: ~0,8k mỗi bước).
MAX_RESULT_CHARS = 3_200
MIN_QUERY_CHARS = 3
SEARCH_AUTHORS = 3
ABSTRACT_CHARS = 600
# Cắt từng trường để một bản ghi bất thường (tiêu đề hay tên rất dài) không chiếm hết kết quả.
TITLE_CHARS = 300
VENUE_CHARS = 120
NAME_CHARS = 80
GET_AUTHORS = 15

UNKNOWN_CANDIDATE_FIX = "Use a candidate_id returned by meta_search in this session."
_WORD_CHAR = re.compile(r"\w")
_SEARCH_STATUS: Mapping[str, str] = {
    "ok": "ok",
    "not_found": "ok",
    "error": "error",
    "circuit_open": "unavailable",
}
_SUCCESS = frozenset({"ok", "not_found"})


class MetaSearchArgs(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    year: int | None = None
    limit: int = Field(5, ge=1, le=10)


class MetaGetArgs(BaseModel):
    candidate_id: str = Field(pattern=r"^cand_[0-9]+$")


class Resolution(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "resolution/1"
    decision: Literal["MATCH", "NO_MATCH"]
    candidate_id: str | None
    note: str = Field(max_length=300)


def _keys(record: WorkRecord) -> list[str]:
    """Khóa nhận diện cùng một tài liệu: DOI, arXiv id, rồi định danh riêng của nguồn."""
    keys: list[str] = []
    doi = normalize_doi(record.doi)
    if doi is not None:
        keys.append(f"doi:{doi}")
    if record.arxiv_id:
        keys.append(f"arxiv:{record.arxiv_id.lower()}")
    keys.append(f"src:{record.source}:{record.source_id}")
    return keys


class MetaSession:
    """Ứng viên đã hiện cho agent trong một phiên: `cand_1…` theo thứ tự gặp lần đầu."""

    def __init__(self) -> None:
        self.candidates: dict[str, WorkRecord] = {}
        self.sources: dict[str, list[str]] = {}
        # Nguồn có ít nhất một lần tìm thành công / một lần lỗi trong phiên.
        self.searched: set[str] = set()
        self.failed: set[str] = set()
        self._by_key: dict[str, str] = {}

    def find(self, record: WorkRecord, keys: Iterable[str] = ()) -> str | None:
        """ID đã gán cho bản ghi (theo khóa của nó, rồi `keys` thêm), chưa có → None."""
        candidates = (*_keys(record), *sorted(keys))
        return next((self._by_key[k] for k in candidates if k in self._by_key), None)

    def add(self, record: WorkRecord, *sources: str, keys: Iterable[str] = ()) -> str:
        """ID của bản ghi (đã có thì dùng lại, gộp nguồn); bản ghi giữ là bản gặp đầu tiên.

        `keys`: khóa thêm của các bản ghi trùng đã gộp vào bản ghi này (ví dụ arXiv id của bản
        từ nguồn khác), để lần tìm sau nhận ra chúng.
        """
        extra = sorted(keys)
        cand_id = self.find(record, extra)
        if cand_id is None:
            cand_id = f"cand_{len(self.candidates) + 1}"
            self.candidates[cand_id] = record
            self.sources[cand_id] = []
        for key in (*_keys(record), *extra):
            self._by_key.setdefault(key, cand_id)
        self.sources[cand_id] += [s for s in sources if s not in self.sources[cand_id]]
        return cand_id

    def record_outcome(self, outcome: LookupOutcome) -> None:
        (self.searched if outcome.status in _SUCCESS else self.failed).add(outcome.source)


def _clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _name(given: str | None, family: str) -> str:
    return _clip(" ".join(p for p in (given, family) if p), NAME_CHARS) or ""


def _concise(cand_id: str, record: WorkRecord, sources: Sequence[str]) -> dict[str, Any]:
    return {
        "candidate_id": cand_id,
        "title": _clip(record.title, TITLE_CHARS),
        "authors": [_name(a.given, a.family) for a in record.authors[:SEARCH_AUTHORS]],
        "year": record.year,
        "venue": _clip(record.venue, VENUE_CHARS),
        "doi": normalize_doi(record.doi),
        "type": record.work_type,
        "sources": list(sources),
    }


def _render(tag: str, payload: Mapping[str, Any]) -> str:
    return render_data_block(tag, json.dumps(payload, ensure_ascii=False), untrusted=True)


class _Group:
    """Một tài liệu trong kết quả của lần tìm hiện tại (đã gộp theo khóa)."""

    def __init__(self, record: WorkRecord, source: str) -> None:
        self.record = record
        self.sources = [source]
        self.keys = set(_keys(record))


class MetaSearchTool:
    name = "meta_search"
    input_model: type[BaseModel] = MetaSearchArgs

    def __init__(self, gateway: MetadataGateway, session: MetaSession, description: str) -> None:
        self.description = description
        self._gateway = gateway
        self._session = session
        self._order = tuple(s for s in SOURCES if s in gateway.sources)

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        assert isinstance(args, MetaSearchArgs)
        query = args.query.strip()
        if len(_WORD_CHAR.findall(query)) < MIN_QUERY_CHARS:
            return ToolError(
                "QUERY_TOO_SHORT",
                "The query has fewer than 3 letters or digits.",
                "Use a query with at least 3 letters or digits, for example the first author's "
                "surname, the year and a distinctive title word.",
            )
        outcomes: list[LookupOutcome] = await asyncio.gather(
            *(
                self._gateway.search_text(source, query, year=args.year, limit=args.limit)
                for source in self._order
            )
        )
        for outcome in outcomes:
            self._session.record_outcome(outcome)
        if not any(outcome.status in _SUCCESS for outcome in outcomes):
            return ToolError(
                "ALL_SOURCES_UNAVAILABLE",
                "No metadata source could be searched right now.",
                "Do not search again. Call submit_resolution with NO_MATCH, or with MATCH only "
                "for a candidate already returned in this session.",
            )
        groups = _merge(outcomes)
        searched = [{"source": o.source, "status": _SEARCH_STATUS[o.status]} for o in outcomes]
        return ToolResult(self._fit(groups[: args.limit], searched))

    def _fit(self, groups: Sequence[_Group], searched: list[dict[str, str]]) -> str:
        """Kết quả trong giới hạn kích thước; chỉ ứng viên hiện ra mới được ghi vào phiên."""
        session = self._session
        shown: list[dict[str, Any]] = []
        next_id = len(session.candidates) + 1
        for group in groups:
            cand_id = session.find(group.record, group.keys)
            if cand_id is None:
                cand_id = f"cand_{next_id}"
                next_id += 1
            sources = list(session.sources.get(cand_id, []))
            sources += [s for s in group.sources if s not in sources]
            # Ứng viên đã có: hiện bản ghi đã lưu, là bản ghi `meta_get` và hậu xử lý sẽ dùng.
            record = session.candidates.get(cand_id, group.record)
            entry = _concise(cand_id, record, sources)
            trial = {"candidates": [*shown, entry], "searched": searched, "truncated": True}
            if len(_render("meta_search_results", trial)) > MAX_RESULT_CHARS:
                break
            shown.append(entry)
        payload: dict[str, Any] = {"candidates": shown, "searched": searched}
        if len(shown) < len(groups):
            payload["truncated"] = True
        for group in groups[: len(shown)]:
            session.add(group.record, *group.sources, keys=group.keys)
        return _render("meta_search_results", payload)


def _merge(outcomes: Sequence[LookupOutcome]) -> list[_Group]:
    """Bản ghi theo thứ tự nguồn rồi thứ hạng, gộp các bản ghi chung DOI hoặc arXiv id."""
    groups: list[_Group] = []
    for outcome in outcomes:
        for record in outcome.records:
            keys = set(_keys(record))
            group = next((g for g in groups if g.keys & keys), None)
            if group is None:
                groups.append(_Group(record, outcome.source))
                continue
            group.keys |= keys
            if outcome.source not in group.sources:
                group.sources.append(outcome.source)
    return groups


class MetaGetTool:
    name = "meta_get"
    input_model: type[BaseModel] = MetaGetArgs

    def __init__(self, session: MetaSession, description: str) -> None:
        self.description = description
        self._session = session

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        assert isinstance(args, MetaGetArgs)
        record = self._session.candidates.get(args.candidate_id)
        if record is None:
            return ToolError(
                "UNKNOWN_CANDIDATE_ID",
                f"{args.candidate_id} was not returned by meta_search in this session.",
                UNKNOWN_CANDIDATE_FIX,
            )
        for max_authors in (GET_AUTHORS, SEARCH_AUTHORS):
            content = _render("meta_get_result", self._full(args.candidate_id, record, max_authors))
            if len(content) <= MAX_RESULT_CHARS:
                break
        return ToolResult(content)

    def _full(self, cand_id: str, record: WorkRecord, max_authors: int) -> dict[str, Any]:
        authors = [_name(a.given, a.family) for a in record.authors[:max_authors]]
        payload: dict[str, Any] = {
            "candidate_id": cand_id,
            "title": _clip(record.title, TITLE_CHARS),
            "authors": authors,
            "identifiers": {
                "doi": normalize_doi(record.doi),
                "arxiv": record.arxiv_id,
                "openalex": record.openalex_id,
                "semantic_scholar": record.s2_id,
                "isbn": record.isbn,
            },
            "venue": _clip(record.venue, VENUE_CHARS),
            "year": record.year,
            "type": record.work_type,
            "language": record.language,
            "abstract": record.abstract[:ABSTRACT_CHARS] if record.abstract else None,
            "sources": self._session.sources.get(cand_id, []),
        }
        if len(record.authors) > max_authors:
            payload["more_authors"] = len(record.authors) - max_authors
        return payload


def make_meta_tools(
    gateway: MetadataGateway, session: MetaSession, descriptions: Mapping[str, str]
) -> tuple[list[Tool], SubmitTool[Resolution]]:
    """`meta_search`, `meta_get` và `submit_resolution` khóa vào `session` và `gateway`."""

    def check(value: Resolution) -> Sequence[ToolError]:
        if value.decision != "MATCH":
            return []
        if value.candidate_id is None:
            return [
                ToolError(
                    "CANDIDATE_REQUIRED_FOR_MATCH",
                    "decision MATCH needs a candidate_id.",
                    "Set candidate_id to a candidate returned by meta_search in this session, "
                    "or choose NO_MATCH with candidate_id null.",
                )
            ]
        if value.candidate_id not in session.candidates:
            return [
                ToolError(
                    "UNKNOWN_CANDIDATE_ID",
                    f"{value.candidate_id} was not returned by meta_search in this session.",
                    UNKNOWN_CANDIDATE_FIX,
                )
            ]
        return []

    tools: list[Tool] = [
        MetaSearchTool(gateway, session, descriptions["meta_search"]),
        MetaGetTool(session, descriptions["meta_get"]),
    ]
    submit = SubmitTool(
        name="submit_resolution",
        description=descriptions["submit_resolution"],
        model=Resolution,
        check=check,
    )
    return tools, submit
