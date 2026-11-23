"""Trang tài khoản và xóa tài khoản (FR-62)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from citeproof.core.errors import AuthError
from citeproof.services import deletion
from citeproof.web.deps import SESSION_COOKIE, AppStorage, Csrf, Db, UserDep
from citeproof.web.rendering import render

router = APIRouter()


@router.get("/account")
async def account_page(request: Request, current: UserDep) -> Response:
    return render(request, "pages/account.html", {}, current=current)


@router.post("/account/delete", dependencies=[Csrf])
async def account_delete(
    request: Request,
    db: Db,
    storage: AppStorage,
    current: UserDep,
    password: Annotated[str, Form()] = "",
) -> Response:
    try:
        await deletion.delete_account(db, storage, user=current.user, password=password)
    except AuthError as exc:
        return render(
            request, "pages/account.html", {"error": str(exc)}, current=current, status=400
        )
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
