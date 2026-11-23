"""Báo cáo một lần chạy: bốn tab, tờ đối chiếu, xuất Markdown (spec web UI §7.5)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import Response

from citeproof.core.enums import ClaimStatus, ReferenceStatus
from citeproof.core.errors import InputError
from citeproof.schemas.report import Overview
from citeproof.services import report
from citeproof.web.deps import CurrentUser, Db, UserDep, is_htmx
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


@router.get("/runs/{run_id}/missing")
async def missing_page(request: Request, db: Db, current: UserDep, run_id: UUID) -> Response:
    overview = await report.get_overview(db, user=current.user, run_id=run_id)
    _, items = await report.list_missing(db, user=current.user, run_id=run_id)
    return _page(
        request, current, overview, "missing", "pages/report_missing.html", {"items": items}
    )


@router.get("/runs/{run_id}/report.md")
async def markdown_export(db: Db, current: UserDep, run_id: UUID) -> Response:
    text = await report.render_markdown(db, user=current.user, run_id=run_id)
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="citeproof-{str(run_id)[:8]}.md"'},
    )
