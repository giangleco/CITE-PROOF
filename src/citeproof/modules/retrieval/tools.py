"""Công cụ của A2 Evidence Investigator (spec §9, `07a` §4.5.3, §4.6.2, §4.6.4; T-704).

Phạm vi khóa bằng closure: `SourceSession.source_file_id` cố định lúc dựng phiên, không đối số
nào của công cụ nêu nguồn hay tài liệu, nên nội dung nguồn (prompt injection) không mở rộng được
phạm vi. ID ngắn theo phiên: `chunk_N` theo `chunk_index` (chunk đầu là `chunk_1`), `sec_N` theo
thứ tự xuất hiện của mục (preflight F49; phiên ánh xạ `sec_N` → tên mục cho `search_chunks`).

- `source_outline`: tiêu đề, mức nội dung và các mục (id, tên, trang, số chunk).
- `source_search`: tìm lexical (`build_tsquery` + `search_chunks`) trong đúng nguồn, k ≤ 8, lọc
  theo mục; bản xem trước 240 ký tự, không đủ để trích.
- `source_read`: toàn văn 1-3 chunk (kèm lân cận khi `neighbors=1`), khoảng 1 500 token; chỉ
  chunk thực sự được trả mới vào `SourceSession.read`.
- `verdict_check`: kiểm tra `submit_verdict` (`UNKNOWN_CHUNK_ID`, `CHUNK_NOT_READ`,
  `QUOTE_NOT_IN_CHUNK`, `EVIDENCE_REQUIRED`).

Mọi văn bản nguồn trong kết quả công cụ nằm trong data block không tin cậy.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import ChunkRegion, ClaimStatus, ContentLevel
from citeproof.core.errors import InternalError
from citeproof.db.models import SourceChunk, SourceFile, Work
from citeproof.llm.context import render_data_block
from citeproof.llm.tools import Tool, ToolError, ToolResult
from citeproof.modules.retrieval.lexical import build_tsquery, search_chunks
from citeproof.modules.verification.context import render_chunks
from citeproof.modules.verification.quotes import locate_quote, unescape_quote
from citeproof.modules.verification.rules import EVIDENCE_MINIMUM, ChunkInfo
from citeproof.modules.verification.verdict import Verdict

__all__ = [
    "PREVIEW_CHARS",
    "READ_MAX_CHARS",
    "SourceReadArgs",
    "SourceSearchArgs",
    "SourceSession",
    "make_source_tools",
    "open_source_session",
    "verdict_check",
]

PREVIEW_CHARS = 240
# Khoảng 1 500 token (~4 ký tự/token), dưới `TOOL_OUTPUT_LIMIT` (6 000) của runner.
READ_MAX_CHARS = 5_800
# Kết quả outline/search: khoảng 1 000 token.
MAX_RESULT_CHARS = 4_000
SECTION_CHARS = 120
TITLE_CHARS = 300


class SourceOutlineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, max_length=300)
    k: int = Field(5, ge=1, le=8)
    section_id: str | None = None


class SourceReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_ids: list[str] = Field(min_length=1, max_length=3)
    neighbors: Literal[0, 1] = 0


@dataclass
class SourceSession:
    """Một nguồn toàn văn đang điều tra; mọi công cụ của phiên khóa vào `source_file_id`."""

    source_file_id: UUID
    title: str | None
    content_level: ContentLevel
    # `chunk_N` → chunk, theo `chunk_index`.
    chunks: dict[str, ChunkInfo]
    # `sec_N` → tên mục (đúng giá trị `source_chunks.section`).
    sections: dict[str, str]
    read: set[str] = field(default_factory=set)
    _order: list[str] = field(default_factory=list, init=False, repr=False)
    _by_uuid: dict[UUID, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._order = list(self.chunks)
        self._by_uuid = {chunk.chunk_id: short for short, chunk in self.chunks.items()}

    def short_id(self, chunk_id: UUID) -> str | None:
        return self._by_uuid.get(chunk_id)

    def neighbors(self, short_id: str) -> list[str]:
        pos = self._order.index(short_id)
        return [self._order[i] for i in (pos - 1, pos + 1) if 0 <= i < len(self._order)]

    def position(self, short_id: str) -> int:
        return self._order.index(short_id)


async def open_source_session(
    session_factory: async_sessionmaker[AsyncSession], source_file_id: UUID
) -> SourceSession:
    """Đọc chunk và metadata của một `source_files` (phiên DB ngắn, chỉ đọc)."""
    async with session_factory() as session:
        head = (
            await session.execute(
                select(SourceFile.content_level, Work.title)
                .outerjoin(Work, Work.id == SourceFile.work_id)
                .where(SourceFile.id == source_file_id)
            )
        ).one_or_none()
        if head is None:
            raise InternalError("A2: không tìm thấy source_file của cặp")
        rows = (
            await session.execute(
                select(SourceChunk)
                .where(SourceChunk.source_file_id == source_file_id)
                .order_by(SourceChunk.chunk_index)
            )
        ).scalars()
        chunks: dict[str, ChunkInfo] = {}
        sections: dict[str, str] = {}
        seen: set[str] = set()
        for row in rows:
            short = f"chunk_{row.chunk_index + 1}"
            chunks[short] = ChunkInfo(
                chunk_id=row.id,
                short_id=short,
                text=row.text,
                page=row.page,
                section=row.section,
                region=ChunkRegion(row.region),
            )
            if row.section and row.section not in seen:
                seen.add(row.section)
                sections[f"sec_{len(sections) + 1}"] = row.section
    return SourceSession(
        source_file_id=source_file_id,
        title=head.title,
        content_level=ContentLevel(head.content_level),
        chunks=chunks,
        sections=sections,
    )


def _clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _render(tag: str, payload: Mapping[str, Any]) -> str:
    return render_data_block(tag, json.dumps(payload, ensure_ascii=False), untrusted=True)


class SourceOutlineTool:
    name = "source_outline"
    input_model: type[BaseModel] = SourceOutlineArgs

    def __init__(self, ss: SourceSession, description: str) -> None:
        self.description = description
        self._ss = ss

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        ss = self._ss
        by_name = {name: sec_id for sec_id, name in ss.sections.items()}
        stats: dict[str, dict[str, Any]] = {}
        unsectioned = 0
        for chunk in ss.chunks.values():
            sec_id = by_name.get(chunk.section or "")
            if sec_id is None:
                unsectioned += 1
                continue
            entry = stats.setdefault(
                sec_id,
                {
                    "section_id": sec_id,
                    "heading": _clip(ss.sections[sec_id], SECTION_CHARS),
                    "pages": [],
                    "chunks": 0,
                    "first_chunk": chunk.short_id,
                },
            )
            entry["chunks"] += 1
            if chunk.page is not None:
                entry["pages"].append(chunk.page)
        sections = []
        for entry in stats.values():
            pages = entry.pop("pages")
            entry["page_range"] = f"{min(pages)}-{max(pages)}" if pages else None
            sections.append(entry)
        payload: dict[str, Any] = {
            "title": _clip(ss.title, TITLE_CHARS),
            "content_level": ss.content_level.value,
            "total_chunks": len(ss.chunks),
            "sections": sections,
        }
        if unsectioned:
            payload["chunks_without_section"] = unsectioned
        while len(_render("source_outline", payload)) > MAX_RESULT_CHARS and payload["sections"]:
            payload["sections"] = payload["sections"][:-1]
            payload["truncated"] = True
        return ToolResult(_render("source_outline", payload))


class SourceSearchTool:
    name = "source_search"
    input_model: type[BaseModel] = SourceSearchArgs

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ss: SourceSession,
        description: str,
    ) -> None:
        self.description = description
        self._session_factory = session_factory
        self._ss = ss

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        assert isinstance(args, SourceSearchArgs)
        ss = self._ss
        section: str | None = None
        if args.section_id is not None:
            section = ss.sections.get(args.section_id)
            if section is None:
                return ToolError(
                    "UNKNOWN_SECTION_ID",
                    f"{args.section_id} is not a section of this source.",
                    "Use a section_id listed by source_outline, or set section_id to null.",
                )
        tsquery = build_tsquery(args.query)
        if tsquery is None:
            return ToolError(
                "QUERY_TOO_SHORT",
                "The query has no searchable word (only stopwords or one-letter words).",
                "Use distinctive content words from the claim, in the source's language: "
                "terms, names, numbers.",
            )
        async with self._session_factory() as session:
            hits = await search_chunks(session, ss.source_file_id, tsquery, args.k, section=section)
        results = []
        for hit in hits:
            short = ss.short_id(hit.chunk_id)
            if short is None:  # chunk mới thêm sau khi mở phiên: không có trong phiên
                continue
            chunk = ss.chunks[short]
            results.append(
                {
                    "chunk_id": short,
                    "section": _clip(chunk.section, SECTION_CHARS),
                    "page": chunk.page,
                    "region": chunk.region.value,
                    "preview": _clip(chunk.text, PREVIEW_CHARS),
                }
            )
        payload: dict[str, Any] = {"results": results}
        if not results:
            payload["note"] = "No chunk matched. Rephrase with other terms or drop section_id."
        return ToolResult(_render("source_search_results", payload))


class SourceReadTool:
    name = "source_read"
    input_model: type[BaseModel] = SourceReadArgs

    def __init__(self, ss: SourceSession, description: str) -> None:
        self.description = description
        self._ss = ss

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        assert isinstance(args, SourceReadArgs)
        ss = self._ss
        unknown = [cid for cid in args.chunk_ids if cid not in ss.chunks]
        if unknown:
            return ToolError(
                "UNKNOWN_CHUNK_ID",
                f"{', '.join(unknown)} is not a chunk of this source.",
                "Use chunk ids returned by source_search or source_outline in this session.",
            )
        wanted = list(dict.fromkeys(args.chunk_ids))
        if args.neighbors:
            extra = [n for cid in wanted for n in ss.neighbors(cid)]
            wanted += [n for n in dict.fromkeys(extra) if n not in wanted]
        chosen: list[ChunkInfo] = []
        not_returned: list[str] = []
        truncated = False
        for cid in wanted:
            chunk = ss.chunks[cid]
            trial = sorted([*chosen, chunk], key=lambda c: ss.position(c.short_id))
            if len(render_chunks(trial)) <= READ_MAX_CHARS:
                chosen = trial
            elif not chosen:  # một chunk dài hơn giới hạn: trả phần đầu
                room = READ_MAX_CHARS - len(render_chunks([_with_text(chunk, "")])) - 20
                chosen = [_with_text(chunk, chunk.text[: max(room, 0)] + "…")]
                truncated = True
            else:
                not_returned.append(cid)
        ss.read.update(c.short_id for c in chosen)
        content = render_chunks(chosen)
        notes = []
        if truncated:
            notes.append("The chunk was cut at the size limit.")
        if not_returned:
            notes.append(
                f"Not returned (size limit): {', '.join(not_returned)}. Read them in another call."
            )
        if notes:
            content += "\n" + " ".join(notes)
        return ToolResult(content)


def _with_text(chunk: ChunkInfo, text: str) -> ChunkInfo:
    return ChunkInfo(chunk.chunk_id, chunk.short_id, text, chunk.page, chunk.section, chunk.region)


def make_source_tools(
    session_factory: async_sessionmaker[AsyncSession],
    ss: SourceSession,
    descriptions: Mapping[str, str],
) -> list[Tool]:
    """`source_outline`, `source_search`, `source_read` khóa vào `ss.source_file_id`."""
    return [
        SourceOutlineTool(ss, descriptions["source_outline"]),
        SourceSearchTool(session_factory, ss, descriptions["source_search"]),
        SourceReadTool(ss, descriptions["source_read"]),
    ]


_REQUIRED_TEXT: Mapping[ClaimStatus, str] = {
    ClaimStatus.SUPPORTED: "at least one SUPPORTS quote",
    ClaimStatus.PARTIALLY_SUPPORTED: "at least one SUPPORTS or PARTIALLY_SUPPORTS quote",
    ClaimStatus.CONTRADICTED: "at least one CONTRADICTS quote",
    ClaimStatus.UNSUPPORTED: "at least one RELATED quote: the closest passage you read",
}


def verdict_check(ss: SourceSession) -> Callable[[Verdict], Sequence[ToolError]]:
    """Kiểm tra nghiệp vụ của `submit_verdict` theo trạng thái phiên (`07a` §4.6.4)."""

    def check(verdict: Verdict) -> list[ToolError]:
        errors: list[ToolError] = []
        for item in verdict.evidence:
            cid = item.chunk_id
            chunk = ss.chunks.get(cid)
            if chunk is None:
                errors.append(
                    ToolError(
                        "UNKNOWN_CHUNK_ID",
                        f"{cid} is not a chunk of this source.",
                        "Use only chunk ids shown in this context "
                        f"(chunk_1…chunk_{len(ss.chunks)}).",
                    )
                )
            elif cid not in ss.read:
                errors.append(
                    ToolError(
                        "CHUNK_NOT_READ",
                        f"{cid} was not read with source_read in this session.",
                        f"Read {cid} with source_read before citing it.",
                    )
                )
            elif locate_quote(unescape_quote(item.quote), chunk.text) is None:
                errors.append(
                    ToolError(
                        "QUOTE_NOT_IN_CHUNK",
                        f"The quote for {cid} does not appear verbatim in that chunk.",
                        f"Copy the quote exactly from the source_read text of {cid}, "
                        "or remove this evidence item.",
                    )
                )
        minimum = EVIDENCE_MINIMUM.get(verdict.status)
        if minimum is not None and not any(e.relation in minimum for e in verdict.evidence):
            status = verdict.status.value
            errors.append(
                ToolError(
                    "EVIDENCE_REQUIRED",
                    f"Status {status} has no evidence item of the required relation.",
                    f"Status {status} needs {_REQUIRED_TEXT[verdict.status]}. "
                    "Add it, or set status to UNVERIFIABLE.",
                )
            )
        return errors

    return check
