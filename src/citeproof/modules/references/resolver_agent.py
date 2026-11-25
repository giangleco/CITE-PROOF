"""A1 Reference Resolver: leo thang của S3 khi chuỗi tra cứu không đủ (spec §9, `07a` §4.5.2).

A1 chạy khi được bật trong cấu hình lần chạy (tắt mặc định, QĐ-47), có `AgentRunner`, và
`should_escalate`: (a) chuỗi tra ra `NOT_FOUND_IN_SEARCHED_SOURCES`, (b) `PARSE_FAILED`, hoặc
(c) hai ứng viên sát điểm; không bao giờ cho `WEB` hay khi mọi nguồn đang bị ngắt mạch.

Mã, không phải LLM, quyết định trạng thái và `needs_user_review` (`verify_agent_candidate`):
ứng viên agent chọn được so với trường parse gốc, hoặc với `raw_text` khi `PARSE_FAILED`; trường
do agent viết lại không bao giờ được dùng. Ba trường hợp:

1. DOI trùng, hoặc tiêu đề khớp (≥ `TITLE_WEAK`) cùng họ tác giả đầu và năm (lệch ≤ 1; bài không
   ghi năm thì cần tiêu đề ≥ `TITLE_ACCEPT`) → `MATCHED`/`METADATA_MISMATCH` như chuỗi tra cứu.
   Với `PARSE_FAILED`: DOI của ứng viên có trong chuỗi gốc, hoặc tiêu đề có trong chuỗi gốc kèm
   họ tác giả đầu hoặc năm (tiêu đề ngắn cần cả hai); trường nào của ứng viên không có trong
   chuỗi gốc thì tính là lệch.
2. Tiêu đề lệch nhưng cùng họ tác giả đầu, cùng năm và cùng venue, không có DOI mâu thuẫn
   (thường do tiêu đề dịch) → `METADATA_MISMATCH` với trường `title` và `needs_user_review`.
3. Còn lại, hoặc agent trả `NO_MATCH` (kể cả `MATCH` không có ứng viên sau khi hết lượt nộp,
   preflight) → giữ nguyên kết quả chuỗi tra cứu; đề xuất của agent chỉ nằm trong `agent_runs`.

Agent hết ngân sách, không nộp hay lỗi → giữ kết quả chuỗi tra cứu và gắn `needs_user_review`.
`RetryableError` và mất lease lan ra ngoài (S3 thử lại job, trừ lần thử cuối). Không log chuỗi
tài liệu tham khảo, truy vấn hay đầu ra LLM.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from functools import cache
from types import MappingProxyType

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import (
    AgentName,
    AgentRunStatus,
    AgentTargetType,
    LLMTask,
    ReferenceStatus,
    RefType,
    ResolvedBy,
    StageName,
)
from citeproof.integrations.metadata import SOURCES, MetadataGateway, WorkRecord
from citeproof.llm.agent_runner import AgentBudget
from citeproof.llm.context import render_data_block
from citeproof.llm.tools import load_tool_catalog
from citeproof.modules.references.cascade import (
    CascadeResult,
    EscalationResult,
    ReferenceEntryView,
    match_status,
)
from citeproof.modules.references.matcher import (
    MAX_YEAR_DELTA,
    SHORT_TITLE_TOKENS,
    TITLE_ACCEPT,
    TITLE_WEAK,
    MatchResult,
    Mismatch,
    author_display,
    first_author_matches,
    match_candidate,
    record_family_keys,
    title_similarity,
    venue_matches,
)
from citeproof.modules.references.normalize import fold, normalize_doi, title_norm
from citeproof.modules.references.tools import MetaSession, Resolution, make_meta_tools
from citeproof.pipeline.config import RunInfo
from citeproof.pipeline.deps import PROMPTS_ROOT, PipelineDeps, call_context

TOOL_CATALOG = "meta"
PROMPT_TASK = LLMTask.AGENT_REFERENCE_RESOLVER.value
_ESCALATE = frozenset({ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES, ReferenceStatus.PARSE_FAILED})
_NAME_CHARS = re.compile(r"[\W_]+")
# DOI và URL trong chuỗi gốc bị bỏ trước khi tìm năm/họ ("cvpr.2016.90" không phải năm in ra).
_LINKS = re.compile(r"(?:https?://|www\.|doi:\s*)\S+|\b10\.\d{4,9}/\S+", re.IGNORECASE)

Verdict = tuple[ReferenceStatus, MatchResult, bool]


def _has_fields(ref: ParsedReference) -> bool:
    return bool(ref.title and title_norm(ref.title)) or normalize_doi(ref.doi) is not None


def verify_agent_candidate(
    ref: ParsedReference | None, raw_text: str, cand: WorkRecord
) -> Verdict | None:
    """Trạng thái, kết quả so khớp và `needs_user_review` cho ứng viên của agent; None = từ chối.

    `ref` là trường parse gốc; None (hoặc không có tiêu đề lẫn DOI hợp lệ) nghĩa là
    `PARSE_FAILED`, khi đó ứng viên được so với `raw_text`.
    """
    if ref is None or not _has_fields(ref):
        return _verify_raw(raw_text, cand)
    return _verify_parsed(ref, cand)


def _verify_parsed(ref: ParsedReference, cand: WorkRecord) -> Verdict | None:
    match = match_candidate(ref, cand)
    ref_doi = normalize_doi(ref.doi)
    cand_doi = normalize_doi(cand.doi)
    if ref_doi is not None and ref_doi == cand_doi:
        return match_status(match), match, match.needs_review
    same_author = bool(ref.authors and cand.authors) and first_author_matches(
        ref.authors[0], cand.authors[0]
    )
    year_delta = (
        abs(ref.year - cand.year) if ref.year is not None and cand.year is not None else None
    )
    year_ok = (year_delta is not None and year_delta <= MAX_YEAR_DELTA) or (
        ref.year is None and match.title_score >= TITLE_ACCEPT
    )
    if match.accepted and match.title_score >= TITLE_WEAK and same_author and year_ok:
        return match_status(match), match, match.needs_review
    doi_conflict = ref_doi is not None and cand_doi is not None
    title_mismatch = any(m.field == "title" for m in match.mismatches)
    if (
        title_mismatch
        and same_author
        and year_delta == 0
        and not doi_conflict
        and venue_matches(ref.venue, cand.venue)
    ):
        return ReferenceStatus.METADATA_MISMATCH, replace(match, accepted=True), True
    return None


def _raw_tokens(raw_text: str) -> tuple[str, set[str]]:
    text = _LINKS.sub(" ", raw_text)
    folded = fold(title_norm(text))
    return text, set(folded.split())


def _verify_raw(raw_text: str, cand: WorkRecord) -> Verdict | None:
    text, tokens = _raw_tokens(raw_text)
    cand_doi = normalize_doi(cand.doi)
    doi_in = cand_doi is not None and cand_doi in raw_text.lower()
    title_score = title_similarity(cand.title, text)
    title_in = title_score >= TITLE_ACCEPT
    author_in = bool(cand.authors) and bool(record_family_keys(cand.authors[0]) & tokens)
    year_in = cand.year is not None and re.search(rf"(?<!\d){cand.year}(?!\d)", text) is not None
    mismatches: list[Mismatch] = []
    if cand.title and not title_in:
        mismatches.append(Mismatch(field="title", in_document=None, found=cand.title))
    if cand.authors and not author_in:
        found = author_display(cand.authors[0])
        mismatches.append(Mismatch(field="authors", in_document=None, found=found))
    if cand.year is not None and not year_in:
        mismatches.append(Mismatch(field="year", in_document=None, found=str(cand.year)))
    short = len(fold(title_norm(cand.title)).split()) < SHORT_TITLE_TOKENS
    corroborated = (author_in and year_in) if short else (author_in or year_in)
    if doi_in or (title_in and corroborated):
        result = MatchResult(
            accepted=True,
            score=1.0 if doi_in else title_score / 100,
            title_score=title_score,
            mismatches=tuple(mismatches),
        )
        return match_status(result), result, False
    venue_in = bool(cand.venue) and title_similarity(cand.venue, text) >= TITLE_ACCEPT
    if not title_in and author_in and year_in and venue_in:
        result = MatchResult(
            accepted=True,
            score=title_score / 100,
            title_score=title_score,
            mismatches=tuple(mismatches),
        )
        return ReferenceStatus.METADATA_MISMATCH, result, True
    return None


def should_escalate(
    entry: ReferenceEntryView, prior: CascadeResult, gateway: MetadataGateway
) -> bool:
    """Điều kiện a đến c của spec §9; không cho `WEB`, không khi mọi nguồn đang bị ngắt mạch."""
    if entry.ref_type is RefType.WEB:
        return False
    if not any(gateway.available(source) for source in gateway.sources):
        return False
    return prior.status in _ESCALATE or prior.close_call


@cache
def _catalog(version: str) -> Mapping[str, str]:
    return MappingProxyType(load_tool_catalog(PROMPTS_ROOT, TOOL_CATALOG, version))


def _ordered(*groups: Iterable[str]) -> tuple[str, ...]:
    seen = {source for group in groups for source in group}
    return tuple(s for s in SOURCES if s in seen) + tuple(sorted(seen - set(SOURCES)))


def _parsed_block(ref: ParsedReference | None) -> str:
    if ref is None:
        return "(none: the entry could not be split into fields)"
    fields = ref.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return render_data_block("parsed_fields", json.dumps(fields, ensure_ascii=False))


def _lookup_block(prior: CascadeResult) -> str:
    summary: dict[str, object] = {
        "status": prior.status.value,
        "sources_searched": list(prior.sources_searched),
        "sources_failed": list(prior.sources_failed),
    }
    if prior.record is not None:
        record = prior.record
        summary["close_call"] = prior.close_call
        summary["top_candidate"] = {
            "title": record.title,
            "first_author": author_display(record.authors[0]) if record.authors else None,
            "year": record.year,
            "venue": record.venue,
            "doi": normalize_doi(record.doi),
        }
    return render_data_block("lookup", json.dumps(summary, ensure_ascii=False))


class AgentResolver:
    """`ReferenceEscalation` của S3 bằng A1."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def maybe_resolve(
        self,
        entry: ReferenceEntryView,
        prior: CascadeResult,
        info: RunInfo,
        gateway: MetadataGateway,
    ) -> EscalationResult | None:
        cfg = info.config.agents.get(AgentName.REFERENCE_RESOLVER.value)
        runner = self._deps.agents
        if cfg is None or not cfg.enabled or runner is None:
            return None
        if not should_escalate(entry, prior, gateway):
            return None
        ref = None if prior.status is ReferenceStatus.PARSE_FAILED else entry.parsed
        catalog_version = info.config.tool_catalogs[TOOL_CATALOG]
        session = MetaSession()
        tools, submit = make_meta_tools(gateway, session, _catalog(catalog_version))
        spec = info.config.task(LLMTask.AGENT_REFERENCE_RESOLVER)
        parts = self._deps.prompts.render(
            PROMPT_TASK,
            spec.prompt_version,
            language=info.document_language,
            reference=render_data_block("reference", entry.raw_text),
            parsed=_parsed_block(ref),
            lookup=_lookup_block(prior),
        )
        outcome = await runner.run(
            agent=AgentName.REFERENCE_RESOLVER,
            version=spec.prompt_version,
            tool_catalog_version=catalog_version,
            parts=parts,
            tools=tools,
            submit=submit,
            budget=AgentBudget.from_config(cfg),
            spec=spec,
            ctx=call_context(self._deps, info, StageName.S3),
            target_type=AgentTargetType.REFERENCE_ENTRY,
            target_id=entry.id,
        )
        run_id = outcome.agent_run_id
        if outcome.status is not AgentRunStatus.SUBMITTED:
            flagged = replace(prior, needs_user_review=True)
            return EscalationResult(flagged, run_id, ResolvedBy.CASCADE)
        accepted = _accepted(outcome.output, session, ref, entry.raw_text, prior)
        if accepted is None:
            return EscalationResult(prior, run_id, ResolvedBy.CASCADE)
        return EscalationResult(accepted, run_id, ResolvedBy.AGENT)


def _accepted(
    output: Resolution | None,
    session: MetaSession,
    ref: ParsedReference | None,
    raw_text: str,
    prior: CascadeResult,
) -> CascadeResult | None:
    """Kết quả mới khi agent nộp `MATCH` một ứng viên trong phiên và ứng viên qua kiểm tra."""
    if output is None or output.decision != "MATCH" or output.candidate_id is None:
        return None
    cand = session.candidates.get(output.candidate_id)
    if cand is None:
        return None
    verdict = verify_agent_candidate(ref, raw_text, cand)
    if verdict is None:
        return None
    status, match, needs_review = verdict
    return CascadeResult(
        status=status,
        record=cand,
        match=match,
        sources_searched=_ordered(prior.sources_searched, session.searched, [cand.source]),
        sources_failed=_ordered(prior.sources_failed, session.failed),
        needs_user_review=needs_review,
        close_call=False,
    )
