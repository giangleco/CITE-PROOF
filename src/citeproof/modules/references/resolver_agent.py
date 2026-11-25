"""A1 Reference Resolver: leo thang của S3 khi chuỗi tra cứu không đủ (spec §9, `07a` §4.5.2).

A1 chạy khi được bật trong cấu hình lần chạy (tắt mặc định, QĐ-47), có `AgentRunner`, và
`should_escalate`: (a) chuỗi tra ra `NOT_FOUND_IN_SEARCHED_SOURCES`, (b) `PARSE_FAILED`, hoặc
(c) hai ứng viên sát điểm; không bao giờ cho `WEB` hay khi mọi nguồn đang bị ngắt mạch.

Mã, không phải LLM, quyết định trạng thái và `needs_user_review` (`verify_agent_candidate`):
ứng viên agent chọn được so với trường parse gốc, hoặc với `raw_text` khi `PARSE_FAILED`; trường
do agent viết lại không bao giờ được dùng. Ba trường hợp:

1. DOI trùng, hoặc tiêu đề khớp (≥ `TITLE_WEAK`) cùng họ tác giả đầu và năm (lệch ≤ 1; bài không
   ghi năm thì cần tiêu đề ≥ `TITLE_ACCEPT`) → `MATCHED`/`METADATA_MISMATCH` như chuỗi tra cứu.
   Với `PARSE_FAILED`: DOI của ứng viên nằm trong số DOI viết trong chuỗi gốc (so bằng sau
   chuẩn hóa), hoặc tiêu đề có trong chuỗi gốc kèm họ tác giả đầu hoặc năm (tiêu đề ngắn cần cả
   hai); trường nào của ứng viên không có trong chuỗi gốc thì tính là lệch; DOI trùng mà tiêu đề
   không có trong chuỗi gốc thì gắn `needs_user_review`.
2. Tiêu đề lệch (không có ngưỡng tiêu đề: thường là tiêu đề dịch hoàn toàn) nhưng cùng năm,
   cùng venue (so đối xứng `venues_equal`, không theo tập con; với `PARSE_FAILED` venue ≥ 2 từ
   phải xuất hiện liền mạch trong chuỗi gốc), cùng họ tác giả đầu và cùng họ tác giả thứ hai khi
   cả hai bên có ≥ 2 tác giả (với `PARSE_FAILED`: khi ứng viên có ≥ 2 tác giả, họ tác giả thứ hai
   phải có trong chuỗi gốc), không có DOI mâu thuẫn → luôn `METADATA_MISMATCH` với trường
   `title` và `needs_user_review`.
3. Còn lại, hoặc agent trả `NO_MATCH` (kể cả `MATCH` không có ứng viên sau khi hết lượt nộp,
   preflight) → giữ nguyên kết quả chuỗi tra cứu; đề xuất của agent chỉ nằm trong `agent_runs`.

Leo thang vì hai ứng viên sát điểm: kết quả của agent vẫn gắn `needs_user_review`, trừ khi DOI
ghi trong bài trùng DOI của ứng viên được chọn.

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

from rapidfuzz import fuzz

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
# DOI viết trong chuỗi gốc; mỗi chuỗi tìm được qua `normalize_doi` rồi so bằng (không so chuỗi con).
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
# Trường hợp 2 (tiêu đề lệch): venue so đối xứng ở mức này.
VENUE_STRICT = 90
# Venue chỉ được tìm trong chuỗi gốc khi có ít nhất chừng này từ ("Nature" một mình không tính).
RAW_VENUE_MIN_TOKENS = 2

Verdict = tuple[ReferenceStatus, MatchResult, bool]


def _has_fields(ref: ParsedReference) -> bool:
    return bool(ref.title and title_norm(ref.title)) or normalize_doi(ref.doi) is not None


def _venue_norm(venue: str | None) -> str:
    return fold(title_norm(venue)) if venue else ""


def venues_equal(left: str | None, right: str | None) -> bool:
    """Venue của bài và của ứng viên giống nhau (A1 trường hợp 2): so đối xứng, không theo tập con.

    `token_sort_ratio` ≥ `VENUE_STRICT` trên tên đã chuẩn hóa, nên "IEEE" không khớp "IEEE
    Transactions on Pattern Analysis…"; tên viết tắt so với tên đầy đủ cũng không khớp và A1
    từ chối, không bao giờ khớp sai. Riêng của A1: chuỗi tra cứu S3 không so venue (QĐ-20).
    """
    a, b = _venue_norm(left), _venue_norm(right)
    return bool(a and b) and fuzz.token_sort_ratio(a, b) >= VENUE_STRICT


def venue_in_raw(venue: str | None, folded_raw: str) -> bool:
    """Venue (ít nhất `RAW_VENUE_MIN_TOKENS` từ) nằm liền mạch trong chuỗi gốc đã chuẩn hóa."""
    phrase = _venue_norm(venue)
    if len(phrase.split()) < RAW_VENUE_MIN_TOKENS:
        return False
    return f" {phrase} " in f" {folded_raw} "


def dois_in_text(text: str) -> set[str]:
    """DOI hợp lệ viết trong chuỗi, đã chuẩn hóa."""
    found = (normalize_doi(m.group(0)) for m in _DOI_IN_TEXT.finditer(text))
    return {doi for doi in found if doi is not None}


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


def _second_author_ok(ref: ParsedReference, cand: WorkRecord) -> bool:
    """Họ tác giả thứ hai khớp khi cả bài lẫn ứng viên có ít nhất hai tác giả."""
    if len(ref.authors) < 2 or len(cand.authors) < 2:
        return True
    return first_author_matches(ref.authors[1], cand.authors[1])


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
        and _second_author_ok(ref, cand)
        and year_delta == 0
        and not doi_conflict
        and venues_equal(ref.venue, cand.venue)
    ):
        return ReferenceStatus.METADATA_MISMATCH, replace(match, accepted=True), True
    return None


def _verify_raw(raw_text: str, cand: WorkRecord) -> Verdict | None:
    text = _LINKS.sub(" ", raw_text)
    folded = fold(title_norm(text))
    tokens = set(folded.split())
    cand_doi = normalize_doi(cand.doi)
    doi_in = cand_doi is not None and cand_doi in dois_in_text(raw_text)
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
        # DOI trùng mà tiêu đề không có trong chuỗi gốc: tự kiểm tra (quy tắc S3 sau Task 9).
        return match_status(result), result, doi_in and not title_in
    if (
        not title_in
        and author_in
        and (len(cand.authors) < 2 or bool(record_family_keys(cand.authors[1]) & tokens))
        and year_in
        and venue_in_raw(cand.venue, folded)
    ):
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
    if prior.close_call:
        # Hai ứng viên sát điểm: lựa chọn của agent vẫn cần người dùng xem lại, trừ khi DOI ghi
        # trong bài trùng DOI ứng viên được chọn (controller, fix round 1).
        ref_doi = normalize_doi(ref.doi) if ref is not None else None
        needs_review = needs_review or ref_doi is None or ref_doi != normalize_doi(cand.doi)
    return CascadeResult(
        status=status,
        record=cand,
        match=match,
        sources_searched=_ordered(prior.sources_searched, session.searched, [cand.source]),
        sources_failed=_ordered(prior.sources_failed, session.failed),
        needs_user_review=needs_review,
        close_call=False,
    )
