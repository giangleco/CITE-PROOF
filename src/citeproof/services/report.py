"""Báo cáo một lần chạy: tổng quan, tài liệu tham khảo, nhận định, tờ đối chiếu, thiếu toàn
văn, xuất Markdown (spec web UI §7.5, `04` §4.8).

Mỗi hàm dùng số truy vấn cố định, không phụ thuộc số dòng. Mọi hàm kiểm tra quyền sở hữu
lần chạy và ném `NotFoundError` cho lần chạy của người khác.

Dữ liệu thật (spec §7.9): chỉ dòng `claim_verifications` của verifier cuối
(`RunConfig.variant`, QĐ-28) được tính và hiển thị; dòng `REFERENCE_UNRESOLVED` (không có
reference) vẫn được liệt kê nhờ outer join; tab "Thiếu toàn văn" gồm cả `REFERENCE_NOT_FOUND`
(QĐ-31).
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    FeedbackTarget,
    FeedbackVerdict,
    ReferenceStatus,
    RefType,
    RunStatus,
    UnverifiableReason,
    Verifier,
)
from citeproof.core.errors import NotFoundError
from citeproof.db.models import (
    Citation,
    CitationLink,
    Claim,
    ClaimVerification,
    Document,
    Evidence,
    Feedback,
    ReferenceCheck,
    ReferenceEntry,
    SourceChunk,
    SourceFile,
    User,
    VerificationRun,
    Work,
)
from citeproof.pipeline.config import RunConfig
from citeproof.schemas.report import (
    ClaimRow,
    ClaimSheet,
    EvidenceView,
    MismatchView,
    MissingSource,
    Overview,
    ReferenceView,
    RunHeader,
    Tally,
    TodoItem,
    WarningView,
)
from citeproof.services.documents import is_sample
from citeproof.web import labels

GENERAL_LIMITATION = (
    "Chỉ đối chiếu nhận định với chính tài liệu được trích, không tìm bằng chứng ở nguồn khác."
)

# Lý do khiến nhận định chờ người dùng tải toàn văn lên (QĐ-31).
MISSING_REASONS = (
    UnverifiableReason.NO_ACCESSIBLE_TEXT.value,
    UnverifiableReason.REFERENCE_NOT_FOUND.value,
)


def _ref_label(ref_index: int | None) -> str:
    return labels.UNRESOLVED_REF if ref_index is None else f"[{ref_index}]"


async def _header(session: AsyncSession, *, user: User, run_id: UUID) -> RunHeader:
    row = (
        await session.execute(
            select(VerificationRun, Document)
            .join(Document, Document.id == VerificationRun.document_id)
            .where(VerificationRun.id == run_id, VerificationRun.user_id == user.id)
        )
    ).first()
    if row is None:
        raise NotFoundError()
    run, document = row
    warnings: list[WarningView] = []
    for item in document.parse_warnings or []:
        if isinstance(item, dict) and isinstance(item.get("code"), str):
            if item["code"] in labels.HIDDEN_WARNINGS:
                continue
            detail = {k: v for k, v in item.items() if k != "code"}
            warnings.append(WarningView(code=item["code"], detail=detail))
    for code in run.warnings or []:
        if isinstance(code, str):
            warnings.append(WarningView(code=code))
    return RunHeader(
        run_id=run.id,
        document_id=document.id,
        document_name=document.filename,
        is_sample=is_sample(document),
        status=RunStatus(run.status),
        finished_at=run.finished_at,
        warnings=warnings,
        verifier=RunConfig.load(run.config).variant,
    )


def _mismatches(raw: Any) -> list[MismatchView]:
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("field"), str):
            in_doc, found = item.get("in_document"), item.get("found")
            result.append(
                MismatchView(
                    field=item["field"],
                    in_document=None if in_doc is None else str(in_doc),
                    found=None if found is None else str(found),
                )
            )
    return result


def _mismatch_text(m: MismatchView) -> str:
    name = labels.MISMATCH_FIELDS.get(m.field, m.field)
    return (
        f"{name}: bài ghi {m.in_document or 'không ghi'}, bản ghi tìm thấy {m.found or '[chưa có]'}"
    )


def _tally(counts: Counter[str], order: Iterable[str]) -> list[Tally]:
    return [Tally(key=key, count=counts[key]) for key in order if counts[key]]


async def get_overview(session: AsyncSession, *, user: User, run_id: UUID) -> Overview:
    header = await _header(session, user=user, run_id=run_id)
    claim_rows = (
        await session.execute(
            select(
                ClaimVerification.status,
                ClaimVerification.needs_user_review,
                ClaimVerification.unverifiable_reason,
                ClaimVerification.reference_entry_id,
            ).where(
                ClaimVerification.run_id == run_id,
                ClaimVerification.verifier == header.verifier.value,
            )
        )
    ).all()
    ref_rows = (
        await session.execute(
            select(ReferenceCheck.status, ReferenceCheck.mismatches, ReferenceEntry.order_index)
            .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
            .where(ReferenceCheck.run_id == run_id)
            .order_by(ReferenceEntry.order_index)
        )
    ).all()
    claim_counts = Counter(status for status, *_ in claim_rows)
    ref_counts = Counter(status for status, *_ in ref_rows)
    base = f"/runs/{run_id}"
    todos: list[TodoItem] = []
    flagged = sum(1 for _, review, _, _ in claim_rows if review)
    if flagged:
        todos.append(
            TodoItem(
                kind="flagged",
                title=f"Xem lại {flagged} nhận định được gắn cờ nên tự kiểm tra",
                detail="Mỗi nhận định có đoạn nguồn nguyên văn để bạn tự so.",
                href=f"{base}/claims?flag=1",
            )
        )
    for status, raw, order_index in ref_rows:
        if status == ReferenceStatus.METADATA_MISMATCH.value:
            fields = "; ".join(_mismatch_text(m) for m in _mismatches(raw))
            todos.append(
                TodoItem(
                    kind="mismatch",
                    title=f"Sửa thông tin của tài liệu [{order_index + 1}]",
                    detail=fields or "Một số trường lệch với bản ghi tìm thấy.",
                    href=f"{base}/references?status={status}",
                )
            )
    missing = len(
        {
            entry
            for _, _, reason, entry in claim_rows
            if reason in MISSING_REASONS and entry is not None
        }
    )
    if missing:
        todos.append(
            TodoItem(
                kind="missing",
                title=f"Tải lên toàn văn của {missing} tài liệu",
                detail="Hệ thống chưa lấy được toàn văn mở của các tài liệu này.",
                href=f"{base}/missing",
            )
        )
    not_found = ref_counts[ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value]
    if not_found:
        todos.append(
            TodoItem(
                kind="not_found",
                title=f"Tự kiểm tra {not_found} tài liệu chưa tìm thấy",
                detail="Luận văn và tài liệu trong nước thường chưa có trong các cơ sở dữ liệu "
                "đã tra.",
                href=f"{base}/references?status=NOT_FOUND_IN_SEARCHED_SOURCES",
            )
        )
    lookup_failed = ref_counts[ReferenceStatus.LOOKUP_FAILED.value]
    if lookup_failed:
        todos.append(
            TodoItem(
                kind="lookup_failed",
                title=f"Kiểm tra lại bài để tra {lookup_failed} tài liệu bị lỗi kết nối",
                detail="Đây là lỗi kết nối tới nguồn tra cứu, không phải kết quả chưa tìm thấy.",
                href=f"{base}/references?status=LOOKUP_FAILED",
            )
        )
    return Overview(
        header=header,
        claim_tally=_tally(claim_counts, (s.value for s in ClaimStatus)),
        ref_tally=_tally(ref_counts, (s.value for s in ReferenceStatus)),
        todos=todos,
        is_empty=not claim_rows and not ref_rows,
        missing_count=missing,
    )


async def list_references(
    session: AsyncSession,
    *,
    user: User,
    run_id: UUID,
    status: ReferenceStatus | None = None,
) -> tuple[RunHeader, list[ReferenceView]]:
    header = await _header(session, user=user, run_id=run_id)
    query = (
        select(ReferenceCheck, ReferenceEntry, Work)
        .join(ReferenceEntry, ReferenceEntry.id == ReferenceCheck.reference_entry_id)
        .outerjoin(Work, Work.id == ReferenceCheck.work_id)
        .where(ReferenceCheck.run_id == run_id)
        .order_by(ReferenceEntry.order_index)
    )
    if status is not None:
        query = query.where(ReferenceCheck.status == status.value)
    cited: dict[UUID, int] = {
        entry_id: int(count)
        for entry_id, count in (
            await session.execute(
                select(CitationLink.reference_entry_id, func.count(CitationLink.id))
                .join(ReferenceEntry, ReferenceEntry.id == CitationLink.reference_entry_id)
                .where(ReferenceEntry.document_id == header.document_id)
                .group_by(CitationLink.reference_entry_id)
            )
        ).all()
        if entry_id is not None
    }
    items = []
    for check, entry, work in (await session.execute(query)).all():
        doi = work.doi if work and work.doi else entry.doi_normalized
        items.append(
            ReferenceView(
                reference_check_id=check.id,
                reference_entry_id=entry.id,
                index=entry.order_index + 1,
                raw_text=entry.raw_text,
                ref_type=RefType(entry.ref_type),
                status=ReferenceStatus(check.status),
                mismatches=_mismatches(check.mismatches),
                sources_searched=list(check.sources_searched or []),
                sources_failed=list(check.sources_failed or []),
                doi=doi,
                url=f"https://doi.org/{doi}" if doi else None,
                needs_user_review=check.needs_user_review,
                cited_count=cited.get(entry.id, 0),
            )
        )
    return header, items


def _claims_query(
    run_id: UUID, verifier: Verifier
) -> Select[ClaimVerification, Claim, Citation, ReferenceEntry]:
    """Dòng của verifier cuối; outer join reference để giữ dòng `REFERENCE_UNRESOLVED`."""
    return (
        select(ClaimVerification, Claim, Citation, ReferenceEntry)
        .join(Claim, Claim.id == ClaimVerification.claim_id)
        .join(Citation, Citation.id == Claim.citation_id)
        .outerjoin(ReferenceEntry, ReferenceEntry.id == ClaimVerification.reference_entry_id)
        .where(ClaimVerification.run_id == run_id, ClaimVerification.verifier == verifier.value)
        .order_by(Citation.page.asc().nulls_last(), Citation.char_start, ClaimVerification.id)
    )


def _row(cv: ClaimVerification, citation: Citation, entry: ReferenceEntry | None) -> ClaimRow:
    return ClaimRow(
        id=cv.id,
        sentence=citation.sentence_text,
        status=ClaimStatus(cv.status),
        needs_user_review=cv.needs_user_review,
        page=citation.page,
        ref_index=entry.order_index + 1 if entry is not None else None,
    )


async def list_claims(
    session: AsyncSession,
    *,
    user: User,
    run_id: UUID,
    status: ClaimStatus | None = None,
    ref_index: int | None = None,
    flagged: bool = False,
) -> tuple[RunHeader, list[ClaimRow]]:
    header = await _header(session, user=user, run_id=run_id)
    query = _claims_query(run_id, header.verifier)
    if status is not None:
        query = query.where(ClaimVerification.status == status.value)
    if ref_index is not None:
        query = query.where(ReferenceEntry.order_index == ref_index - 1)
    if flagged:
        query = query.where(ClaimVerification.needs_user_review.is_(True))
    rows = (await session.execute(query)).all()
    return header, [_row(cv, citation, entry) for cv, _, citation, entry in rows]


async def _evidence_for(
    session: AsyncSession, verification_ids: Sequence[UUID]
) -> dict[UUID, list[EvidenceView]]:
    if not verification_ids:
        return {}
    rows = (
        await session.execute(
            select(Evidence, SourceChunk, SourceFile, Work)
            .join(SourceChunk, SourceChunk.id == Evidence.source_chunk_id)
            .join(SourceFile, SourceFile.id == SourceChunk.source_file_id)
            .outerjoin(Work, Work.id == SourceFile.work_id)
            .where(Evidence.claim_verification_id.in_(verification_ids))
            .order_by(Evidence.created_at, Evidence.id)
        )
    ).all()
    result: dict[UUID, list[EvidenceView]] = {}
    for evidence, chunk, source, work in rows:
        result.setdefault(evidence.claim_verification_id, []).append(
            EvidenceView(
                relation=EvidenceRelation(evidence.relation),
                quote=evidence.quote_text,
                page=chunk.page,
                section=chunk.section,
                language=chunk.language,
                url=source.url,
                doi=work.doi if work else None,
            )
        )
    return result


def _sheet(
    cv: ClaimVerification,
    claim: Claim,
    citation: Citation,
    entry: ReferenceEntry | None,
    evidence: list[EvidenceView],
    feedback: Feedback | None,
) -> ClaimSheet:
    row = _row(cv, citation, entry)
    return ClaimSheet(
        **row.model_dump(),
        span=claim.claim_text,
        marker=citation.marker_text,
        section=citation.section,
        ref_raw=entry.raw_text if entry is not None else None,
        unverifiable_reason=(
            UnverifiableReason(cv.unverifiable_reason) if cv.unverifiable_reason else None
        ),
        explanation=cv.explanation,
        limitations=cv.limitations,
        content_level_used=ContentLevelUsed(cv.content_level_used),
        evidence=evidence,
        model=cv.model,
        prompt_version=cv.prompt_version,
        verifier=cv.verifier,
        my_feedback=FeedbackVerdict(feedback.verdict) if feedback else None,
        my_comment=feedback.comment if feedback else None,
    )


async def get_claim_sheet(
    session: AsyncSession, *, user: User, run_id: UUID, claim_verification_id: UUID
) -> ClaimSheet:
    header = await _header(session, user=user, run_id=run_id)
    row = (
        await session.execute(
            _claims_query(run_id, header.verifier).where(
                ClaimVerification.id == claim_verification_id
            )
        )
    ).first()
    if row is None:
        raise NotFoundError()
    cv, claim, citation, entry = row
    evidence = await _evidence_for(session, [cv.id])
    feedback = (
        await session.execute(
            select(Feedback).where(
                Feedback.user_id == user.id,
                Feedback.target_type == FeedbackTarget.CLAIM_VERIFICATION.value,
                Feedback.target_id == cv.id,
            )
        )
    ).scalar_one_or_none()
    return _sheet(cv, claim, citation, entry, evidence.get(cv.id, []), feedback)


async def list_missing(
    session: AsyncSession, *, user: User, run_id: UUID
) -> tuple[RunHeader, list[MissingSource]]:
    header = await _header(session, user=user, run_id=run_id)
    rows = (
        await session.execute(
            select(
                ReferenceEntry.order_index,
                ReferenceEntry.raw_text,
                func.count(ClaimVerification.id),
                # Một reference có một trạng thái nên thường chỉ có một lý do; nếu có cả hai
                # thì lấy `NO_ACCESSIBLE_TEXT` (đứng trước theo thứ tự chữ).
                func.min(ClaimVerification.unverifiable_reason),
            )
            .join(ClaimVerification, ClaimVerification.reference_entry_id == ReferenceEntry.id)
            .where(
                ClaimVerification.run_id == run_id,
                ClaimVerification.verifier == header.verifier.value,
                ClaimVerification.unverifiable_reason.in_(MISSING_REASONS),
            )
            .group_by(ReferenceEntry.id, ReferenceEntry.order_index, ReferenceEntry.raw_text)
            .order_by(ReferenceEntry.order_index)
        )
    ).all()
    return header, [
        MissingSource(
            ref_index=index + 1,
            raw_text=raw,
            waiting_claims=int(count),
            reason=UnverifiableReason(str(reason)),  # lọc theo MISSING_REASONS: không NULL
        )
        for index, raw, count, reason in rows
    ]


def _md_quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.splitlines() or [""])


async def render_markdown(session: AsyncSession, *, user: User, run_id: UUID) -> str:
    overview = await get_overview(session, user=user, run_id=run_id)
    _, references = await list_references(session, user=user, run_id=run_id)
    rows = (await session.execute(_claims_query(run_id, overview.header.verifier))).all()
    evidence = await _evidence_for(session, [cv.id for cv, *_ in rows])
    sheets = [
        _sheet(cv, claim, citation, entry, evidence.get(cv.id, []), None)
        for cv, claim, citation, entry in rows
    ]
    header = overview.header
    out: list[str] = [f"# Báo cáo kiểm tra trích dẫn: {header.document_name}", ""]
    if header.is_sample:
        out += ["_Bài mẫu: dữ liệu minh họa, không phải kết quả thật._", ""]

    out += ["## Tóm tắt", ""]
    claim_total = sum(t.count for t in overview.claim_tally)
    ref_total = sum(t.count for t in overview.ref_tally)
    flagged = sum(1 for s in sheets if s.needs_user_review)
    out += [
        f"- {ref_total} tài liệu tham khảo, {claim_total} nhận định có trích dẫn.",
        f"- {flagged} nhận định được gắn cờ nên tự kiểm tra.",
        "",
    ]

    out += ["## Thông tin tài liệu và cảnh báo", "", f"- Tài liệu: {header.document_name}"]
    out.append(f"- Trạng thái lần chạy: {labels.RUN_STATUS[header.status].label}")
    for warning in header.warnings:
        out.append(f"- Cảnh báo: {labels.warning_text(warning.code, warning.detail)}")
    out.append("")

    out += ["## Thống kê", "", "| Nhóm | Trạng thái | Số lượng |", "|---|---|---|"]
    for t in overview.claim_tally:
        out.append(f"| Nhận định | {labels.CLAIM_STATUS[ClaimStatus(t.key)].label} | {t.count} |")
    for t in overview.ref_tally:
        label = labels.REF_STATUS[ReferenceStatus(t.key)].label
        out.append(f"| Tài liệu tham khảo | {label} | {t.count} |")
    out.append("")

    out += ["## Tài liệu tham khảo", ""]
    for ref in references:
        out.append(f"{ref.index}. {ref.raw_text}")
        out.append(f"   - Trạng thái: {labels.REF_STATUS[ref.status].label}")
        for m in ref.mismatches:
            out.append(f"   - {_mismatch_text(m)}")
        if ref.status == ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES:
            names = ", ".join(labels.source_name(s) for s in ref.sources_searched)
            out.append(f"   - Đã tra: {names}")
        if ref.doi:
            out.append(f"   - DOI: {ref.doi}")
        if ref.cited_count == 0:
            out.append(f"   - {labels.UNCITED_REFERENCE}")
    out.append("")

    out += ["## Nhận định", ""]
    for sheet in (s for s in sheets if s.status != ClaimStatus.UNVERIFIABLE):
        flag = " (nên tự kiểm tra)" if sheet.needs_user_review else ""
        out += [
            f"### Trang {sheet.page or '[chưa có]'}, tài liệu {_ref_label(sheet.ref_index)}{flag}",
            "",
            f"- Câu trong bài: {sheet.sentence}",
            f"- Trạng thái: {labels.CLAIM_STATUS[sheet.status].label}",
            f"- Dựa trên: {labels.CONTENT_LEVEL[sheet.content_level_used]}",
            f"- Giải thích: {sheet.explanation}",
            "",
        ]
        for ev in sheet.evidence:
            where = ", ".join(
                x for x in (f"trang {ev.page}" if ev.page else "", ev.section or "") if x
            )
            out += [_md_quote(ev.quote), "", f"  {labels.RELATIONS[ev.relation]}; {where}", ""]

    out += ["## Nhận định chưa kiểm chứng được", ""]
    for sheet in (s for s in sheets if s.status == ClaimStatus.UNVERIFIABLE):
        reason = labels.REASONS[sheet.unverifiable_reason] if sheet.unverifiable_reason else ""
        out.append(
            f"- Trang {sheet.page or '[chưa có]'}, tài liệu {_ref_label(sheet.ref_index)}: "
            f"{sheet.sentence} ({reason})"
        )
    out.append("")

    out += ["## Giới hạn", "", f"- {GENERAL_LIMITATION}", ""]
    out += ["## Gợi ý hành động", ""]
    out += [f"- {todo.title}. {todo.detail}" for todo in overview.todos] or ["- Không có."]
    out.append("")
    return "\n".join(out)
