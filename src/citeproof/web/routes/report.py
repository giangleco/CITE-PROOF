"""Báo cáo một lần chạy: bốn tab, tờ đối chiếu, xuất Markdown (spec web UI §7.5)."""

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from citeproof.core.enums import ClaimStatus, FeedbackVerdict, ReferenceStatus
from citeproof.core.errors import CiteProofError, InputError
from citeproof.schemas.report import Overview
from citeproof.services import documents, feedback, report, sources
from citeproof.web.deps import (
    AppSettings,
    AppStorage,
    Csrf,
    CurrentUser,
    Db,
    UserDep,
    is_htmx,
    rollback_keeping_user,
)
from citeproof.web.rendering import render

router = APIRouter()


def _enum(cls: type[ClaimStatus] | type[ReferenceStatus], value: str | None) -> Any:
    if value in (None, ""):
        return None
    try:
        return cls(value)
    except ValueError:
        raise InputError("Bộ lọc trạng thái không hợp lệ.") from None


def _int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    if not value.isdigit():
        raise InputError("Số thứ tự tài liệu không hợp lệ.")
    return int(value)


def _uuid(value: str | None) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(value)
    except ValueError:
        raise InputError("Mã nhận định không hợp lệ.") from None


def _page(
    request: Request,
    current: CurrentUser,
    overview: Overview,
    tab: str,
    name: str,
    context: dict[str, Any],
) -> Response:
    return render(
        request,
        name,
        {"overview": overview, "header": overview.header, "tab": tab, **context},
        current=current,
        nav="documents",
    )


@router.get("/runs/{run_id}/report")
async def overview_page(request: Request, db: Db, current: UserDep, run_id: UUID) -> Response:
    overview = await report.get_overview(db, user=current.user, run_id=run_id)
    return _page(request, current, overview, "overview", "pages/report_overview.html", {})


@router.get("/runs/{run_id}/references")
async def references_page(
    request: Request, db: Db, current: UserDep, run_id: UUID, status: str | None = None
) -> Response:
    chosen = _enum(ReferenceStatus, status)
    overview = await report.get_overview(db, user=current.user, run_id=run_id)
    _, items = await report.list_references(db, user=current.user, run_id=run_id, status=chosen)
    return _page(
        request,
        current,
        overview,
        "references",
        "pages/report_references.html",
        {"items": items, "status": chosen},
    )


async def _claims(
    request: Request,
    db: Db,
    current: CurrentUser,
    run_id: UUID,
    *,
    status: str | None,
    ref: str | None,
    flag: str | None,
    selected: UUID | None,
) -> Response:
    chosen = _enum(ClaimStatus, status)
    ref_index = _int(ref)
    flagged = flag == "1"
    overview = await report.get_overview(db, user=current.user, run_id=run_id)
    _, rows = await report.list_claims(
        db, user=current.user, run_id=run_id, status=chosen, ref_index=ref_index, flagged=flagged
    )
    target = selected if selected and any(r.id == selected for r in rows) else None
    if target is None and rows:
        target = rows[0].id
    sheet = (
        await report.get_claim_sheet(
            db, user=current.user, run_id=run_id, claim_verification_id=target
        )
        if target
        else None
    )
    params = []
    if chosen:
        params.append(f"status={chosen.value}")
    if ref_index:
        params.append(f"ref={ref_index}")
    if flagged:
        params.append("flag=1")
    return _page(
        request,
        current,
        overview,
        "claims",
        "pages/report_claims.html",
        {
            "rows": rows,
            "sheet": sheet,
            "status": chosen,
            "flagged": flagged,
            "ref_index": ref_index,
            "filter_qs": "&".join(params),
        },
    )


@router.get("/runs/{run_id}/claims")
async def claims_page(
    request: Request,
    db: Db,
    current: UserDep,
    run_id: UUID,
    status: str | None = None,
    ref: str | None = None,
    flag: str | None = None,
    c: str | None = None,
) -> Response:
    return await _claims(
        request, db, current, run_id, status=status, ref=ref, flag=flag, selected=_uuid(c)
    )


@router.get("/runs/{run_id}/claims/{claim_verification_id}")
async def claim_sheet(
    request: Request, db: Db, current: UserDep, run_id: UUID, claim_verification_id: UUID
) -> Response:
    if not is_htmx(request):
        return await _claims(
            request,
            db,
            current,
            run_id,
            status=None,
            ref=None,
            flag=None,
            selected=claim_verification_id,
        )
    sheet = await report.get_claim_sheet(
        db, user=current.user, run_id=run_id, claim_verification_id=claim_verification_id
    )
    return render(
        request, "partials/claim_sheet.html", {"sheet": sheet, "run_id": run_id}, current=current
    )


async def _missing(
    request: Request,
    db: Db,
    current: CurrentUser,
    run_id: UUID,
    *,
    status: int = 200,
    error: str | None = None,
) -> Response:
    overview = await report.get_overview(db, user=current.user, run_id=run_id)
    _, items = await report.list_missing(db, user=current.user, run_id=run_id)
    response = _page(
        request,
        current,
        overview,
        "missing",
        "pages/report_missing.html",
        {"items": items, "upload_error": error},
    )
    response.status_code = status
    return response


@router.get("/runs/{run_id}/missing")
async def missing_page(request: Request, db: Db, current: UserDep, run_id: UUID) -> Response:
    return await _missing(request, db, current, run_id)


@router.post("/runs/{run_id}/missing/{reference_entry_id}/upload", dependencies=[Csrf])
async def upload_source(
    request: Request,
    db: Db,
    storage: AppStorage,
    settings: AppSettings,
    current: UserDep,
    run_id: UUID,
    reference_entry_id: UUID,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    try:
        if file is None:
            raise InputError("Hãy chọn một tệp PDF.", code="EMPTY_FILE")
        data = await documents.read_upload(file, settings.max_upload_bytes)
        await sources.upload_source(
            db,
            storage,
            settings,
            user=current.user,
            run_id=run_id,
            reference_entry_id=reference_entry_id,
            data=data,
        )
    except InputError as exc:
        await rollback_keeping_user(db, current)
        code = 413 if exc.code == "FILE_TOO_LARGE" else 400
        return await _missing(request, db, current, run_id, status=code, error=str(exc))
    except CiteProofError:
        await rollback_keeping_user(db, current)
        raise
    await db.commit()
    return RedirectResponse(f"/runs/{run_id}/missing", status_code=303)


@router.post("/runs/{run_id}/sources/{source_file_id}/discard", dependencies=[Csrf])
async def discard_source(
    db: Db, storage: AppStorage, current: UserDep, run_id: UUID, source_file_id: UUID
) -> Response:
    await sources.discard_source(
        db, storage, user=current.user, run_id=run_id, source_file_id=source_file_id
    )
    return RedirectResponse(f"/runs/{run_id}/missing", status_code=303)


@router.get("/runs/{run_id}/report.md")
async def markdown_export(db: Db, current: UserDep, run_id: UUID) -> Response:
    text = await report.render_markdown(db, user=current.user, run_id=run_id)
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="citeproof-{str(run_id)[:8]}.md"'},
    )


@router.post("/claim-verifications/{claim_verification_id}/feedback", dependencies=[Csrf])
async def submit_feedback(
    request: Request,
    db: Db,
    current: UserDep,
    claim_verification_id: UUID,
    verdict: Annotated[str, Form()] = "",
    comment: Annotated[str | None, Form()] = None,
) -> Response:
    run_id = await feedback.owning_run_id(
        db, user=current.user, claim_verification_id=claim_verification_id
    )
    try:
        chosen = FeedbackVerdict(verdict)
    except ValueError:
        raise InputError("Lựa chọn phản hồi không hợp lệ.") from None
    error: str | None = None
    try:
        await feedback.save_feedback(
            db,
            user=current.user,
            claim_verification_id=claim_verification_id,
            verdict=chosen,
            comment=comment,
        )
        await db.commit()
    except InputError as exc:
        await rollback_keeping_user(db, current)
        if not is_htmx(request):
            raise
        error = str(exc)
    if not is_htmx(request):
        return RedirectResponse(f"/runs/{run_id}/claims?c={claim_verification_id}", status_code=303)
    sheet = await report.get_claim_sheet(
        db, user=current.user, run_id=run_id, claim_verification_id=claim_verification_id
    )
    headers = {} if error else {"HX-Trigger": json.dumps({"toast": "Đã lưu phản hồi"})}
    return render(
        request,
        "partials/feedback.html",
        {"sheet": sheet, "run_id": run_id, "feedback_error": error},
        current=current,
        headers=headers,
    )
