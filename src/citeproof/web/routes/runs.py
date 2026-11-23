"""Trang tiến độ, mảnh HTMX tự cập nhật, tiếp tục sau lỗi (spec web UI §7.4)."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from citeproof.core.enums import RunStatus
from citeproof.services import runs_view
from citeproof.web.deps import AppSettings, Csrf, Db, UserDep
from citeproof.web.rendering import render

router = APIRouter()
FINISHED = {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}


def _heading(view: runs_view.RunView) -> str:
    if view.status in FINISHED:
        return "Kiểm tra xong"
    if view.status == RunStatus.FAILED:
        return "Kiểm tra tạm dừng"
    if view.status == RunStatus.CANCELLED:
        return "Đã hủy kiểm tra"
    return "Đang kiểm tra"


@router.get("/runs/{run_id}")
async def run_page(request: Request, db: Db, current: UserDep, run_id: UUID) -> Response:
    view = await runs_view.get_run_view(db, user=current.user, run_id=run_id)
    tech = (
        await runs_view.get_tech_details(db, user=current.user, run_id=run_id)
        if current.is_admin
        else None
    )
    return render(
        request,
        "pages/run.html",
        {"view": view, "tech": tech, "heading": _heading(view), "finished": FINISHED},
        current=current,
        nav="documents",
    )


@router.get("/runs/{run_id}/progress")
async def run_progress(request: Request, db: Db, current: UserDep, run_id: UUID) -> Response:
    view = await runs_view.get_run_view(db, user=current.user, run_id=run_id)
    return render(
        request, "partials/progress.html", {"view": view, "finished": FINISHED}, current=current
    )


@router.post("/runs/{run_id}/resume", dependencies=[Csrf])
async def run_resume(db: Db, settings: AppSettings, current: UserDep, run_id: UUID) -> Response:
    await runs_view.resume(db, settings, user=current.user, run_id=run_id)
    await db.commit()
    return RedirectResponse(f"/runs/{run_id}", status_code=303)
