"""Tiện ích chỉ có ở môi trường local: thêm bài mẫu (spec web UI §7.8)."""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, Response

from citeproof.core.errors import NotFoundError
from citeproof.services.sample import seed_sample
from citeproof.web.deps import AppSettings, Csrf, Db, UserDep

router = APIRouter()


@router.post("/dev/sample", dependencies=[Csrf])
async def add_sample(db: Db, settings: AppSettings, current: UserDep) -> Response:
    if settings.env != "local":
        raise NotFoundError()
    _, run_id = await seed_sample(db, settings, user_id=current.user.id)
    await db.commit()
    return RedirectResponse(f"/runs/{run_id}/report", status_code=303)
