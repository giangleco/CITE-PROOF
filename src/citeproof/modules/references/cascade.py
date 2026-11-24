"""Chuỗi tra cứu tất định của S3 (spec §7.3, QĐ-19; `07a` §4.3.5).

Thứ tự cho một mục:
1. Không có tiêu đề lẫn DOI hợp lệ → `PARSE_FAILED`, không tra.
2. `WEB`, `THESIS`, `OTHER` không có DOI → `UNSUPPORTED_TYPE`, không tra (độ phủ kém, "chưa
   tìm thấy" sẽ gây hiểu lầm). Có DOI thì luôn tra.
3. Có DOI → Crossref theo DOI, rồi OpenAlex theo DOI.
4. Chưa có ứng viên được chấp nhận và bài có tiêu đề → tìm Crossref (bibliographic), OpenAlex,
   Semantic Scholar theo thứ tự; dừng ở nguồn đầu tiên có ứng viên được chấp nhận. Không có
   tiêu đề thì không tìm kiếm: truy vấn rỗng không gọi API nào nên không được tính là đã tra.

`sources_searched` chỉ gồm nguồn mà mọi lần tra trong mục này đều thành công (`ok` hoặc
`not_found`, kể cả lấy từ cache); nguồn có một lần lỗi hay bị ngắt mạch nằm trong
`sources_failed`. Không nguồn nào tra thành công → `LOOKUP_FAILED`, không bao giờ
`NOT_FOUND_IN_SEARCHED_SOURCES` với danh sách rỗng (CHECK `not_found_needs_sources`).

Lỗi của nguồn đã thành kết quả `error` trong gateway; lỗi khác (lỗi lập trình, DB, cache)
lan ra ngoài để stage lỗi.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ReferenceStatus, RefType, ResolvedBy
from citeproof.integrations.metadata import SOURCES, LookupOutcome, MetadataGateway, WorkRecord
from citeproof.modules.references.matcher import BestMatch, MatchResult, pick_best
from citeproof.modules.references.normalize import normalize_doi, title_norm
from citeproof.pipeline.config import RunInfo

DOI_SOURCES: tuple[str, ...] = ("crossref", "openalex")
UNSUPPORTED_WITHOUT_DOI = frozenset({RefType.WEB, RefType.THESIS, RefType.OTHER})
_SUCCESS = frozenset({"ok", "not_found"})


@dataclass(frozen=True)
class ReferenceEntryView:
    """Một `reference_entries` đã đọc cho S3 (trường đã parse ở S2, dạng NFC)."""

    id: UUID
    raw_text: str
    parsed: ParsedReference
    ref_type: RefType


@dataclass(frozen=True)
class CascadeResult:
    status: ReferenceStatus
    record: WorkRecord | None
    match: MatchResult | None
    sources_searched: tuple[str, ...]
    sources_failed: tuple[str, ...]
    needs_user_review: bool
    close_call: bool


class ReferenceResolver(Protocol):
    async def resolve(
        self, entry: ReferenceEntryView, prior: CascadeResult | None
    ) -> CascadeResult: ...


@dataclass(frozen=True)
class EscalationResult:
    """Kết quả sau bước leo thang (A1, Task 25): kết quả cuối và ai quyết định nó."""

    result: CascadeResult
    agent_run_id: UUID | None
    resolved_by: ResolvedBy


class ReferenceEscalation(Protocol):
    async def maybe_resolve(
        self, entry: ReferenceEntryView, prior: CascadeResult, info: RunInfo
    ) -> EscalationResult | None:
        """None: agent không chạy, giữ `prior`."""
        ...


def match_status(match: MatchResult) -> ReferenceStatus:
    return ReferenceStatus.METADATA_MISMATCH if match.mismatches else ReferenceStatus.MATCHED


def _fixed(status: ReferenceStatus) -> CascadeResult:
    """Kết quả không tra nguồn nào (`PARSE_FAILED`, `UNSUPPORTED_TYPE`)."""
    return CascadeResult(
        status=status,
        record=None,
        match=None,
        sources_searched=(),
        sources_failed=(),
        needs_user_review=False,
        close_call=False,
    )


class _SourceTally:
    """Nguồn đã tra thành công và nguồn lỗi trong một mục, theo thứ tự `SOURCES`."""

    def __init__(self, order: Iterable[str]) -> None:
        self._order = tuple(order)
        self._ok: set[str] = set()
        self._failed: set[str] = set()

    def add(self, outcome: LookupOutcome) -> None:
        target = self._ok if outcome.status in _SUCCESS else self._failed
        target.add(outcome.source)

    @property
    def searched(self) -> tuple[str, ...]:
        return tuple(s for s in self._order if s in self._ok and s not in self._failed)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(s for s in self._order if s in self._failed)


class CascadeResolver:
    """Chuỗi tra cứu tuần tự qua `MetadataGateway` (một gateway cho mỗi lần chạy S3)."""

    def __init__(self, gateway: MetadataGateway) -> None:
        self._gateway = gateway
        available = set(gateway.sources)
        self._order = tuple(s for s in SOURCES if s in available)

    async def resolve(
        self, entry: ReferenceEntryView, prior: CascadeResult | None
    ) -> CascadeResult:
        """Kết quả tra cứu của một mục; `prior` không dùng (chuỗi tra luôn chạy từ đầu)."""
        ref = entry.parsed
        doi = normalize_doi(ref.doi)
        has_title = bool(ref.title and title_norm(ref.title))
        if not has_title and doi is None:
            return _fixed(ReferenceStatus.PARSE_FAILED)
        if doi is None and entry.ref_type in UNSUPPORTED_WITHOUT_DOI:
            return _fixed(ReferenceStatus.UNSUPPORTED_TYPE)
        tally = _SourceTally(self._order)
        if doi is not None:
            for source in (s for s in DOI_SOURCES if s in self._order):
                outcome = await self._gateway.by_doi(source, doi)
                tally.add(outcome)
                best = pick_best(ref, outcome.records)
                if best is not None:
                    return _found(best, tally)
        if has_title:
            for source in self._order:
                outcome = await self._gateway.search(source, ref)
                tally.add(outcome)
                best = pick_best(ref, outcome.records)
                if best is not None:
                    return _found(best, tally)
        status = (
            ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES
            if tally.searched
            else ReferenceStatus.LOOKUP_FAILED
        )
        return CascadeResult(
            status=status,
            record=None,
            match=None,
            sources_searched=tally.searched,
            sources_failed=tally.failed,
            needs_user_review=False,
            close_call=False,
        )


def _found(best: BestMatch, tally: _SourceTally) -> CascadeResult:
    return CascadeResult(
        status=match_status(best.result),
        record=best.record,
        match=best.result,
        sources_searched=tally.searched,
        sources_failed=tally.failed,
        needs_user_review=best.close_call or best.result.needs_review,
        close_call=best.close_call,
    )
