"""Đăng nhập, đăng ký bằng mã mời, đăng xuất, đồng ý dữ liệu (spec web UI §7.2)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from citeproof.core.errors import AuthError, InputError, RateLimitedError
from citeproof.services import auth
from citeproof.web.deps import (
    SESSION_COOKIE,
    AppSettings,
    Csrf,
    Db,
    MaybeUser,
    UserDep,
)
from citeproof.web.rendering import render

router = APIRouter()
DEFAULT_TARGET = "/documents"


def safe_next(target: str | None) -> str:
    """Chỉ nhận đường dẫn nội bộ; chặn `//host`, `/\\host` và URL tuyệt đối."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return DEFAULT_TARGET
    if any(ch in target for ch in "\r\n\t"):
        return DEFAULT_TARGET
    return target


def _set_session_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86_400,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies(),
        path="/",
    )


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(DEFAULT_TARGET, status_code=303)


@router.get("/login")
async def login_page(request: Request, current: MaybeUser, next: str | None = None) -> Response:
    if current is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "pages/login.html", {"next": safe_next(next), "email": ""})


@router.post("/login", dependencies=[Csrf])
async def login_submit(
    request: Request,
    db: Db,
    settings: AppSettings,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str | None, Form()] = None,
) -> Response:
    ip = request.client.host if request.client else "unknown"
    try:
        user = await auth.authenticate(db, settings, email=email, password=password, ip=ip)
    except (AuthError, RateLimitedError) as exc:
        await db.commit()
        status = 429 if isinstance(exc, RateLimitedError) else 401
        return render(
            request,
            "pages/login.html",
            {"next": safe_next(next), "email": email, "error": str(exc)},
            status=status,
        )
    token = await auth.create_session(db, settings, user_id=user.id)
    await db.commit()
    response = RedirectResponse(safe_next(next), status_code=303)
    _set_session_cookie(response, token, settings)
    return response


@router.get("/register")
async def register_page(request: Request) -> Response:
    return render(request, "pages/register.html", {"form": {}})


@router.post("/register", dependencies=[Csrf])
async def register_submit(
    request: Request,
    db: Db,
    settings: AppSettings,
    code: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    try:
        user = await auth.register_with_invite(
            db, code=code, email=email, password=password, display_name=display_name
        )
        token = await auth.create_session(db, settings, user_id=user.id)
        await db.commit()
    except InputError as exc:
        await db.rollback()
        form = {"code": code, "email": email, "display_name": display_name}
        return render(
            request,
            "pages/register.html",
            {"form": form, "error": str(exc), "error_code": exc.code},
            status=400,
        )
    response = RedirectResponse("/consent", status_code=303)
    _set_session_cookie(response, token, settings)
    return response


@router.post("/logout", dependencies=[Csrf])
async def logout(db: Db, current: UserDep) -> Response:
    await auth.delete_session(db, current.token)
    await db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/consent")
async def consent_page(
    request: Request, current: UserDep, settings: AppSettings, next: str | None = None
) -> Response:
    return render(
        request,
        "pages/consent.html",
        {
            "next": safe_next(next),
            "provider": settings.llm_provider_name,
            "policy_url": settings.llm_provider_policy_url,
            "hide_nav": True,
        },
        current=current,
    )


@router.post("/consent", dependencies=[Csrf])
async def consent_submit(
    request: Request,
    db: Db,
    current: UserDep,
    settings: AppSettings,
    agree: Annotated[str | None, Form()] = None,
    next: Annotated[str | None, Form()] = None,
) -> Response:
    if agree != "on":
        return render(
            request,
            "pages/consent.html",
            {
                "next": safe_next(next),
                "provider": settings.llm_provider_name,
                "policy_url": settings.llm_provider_policy_url,
                "hide_nav": True,
                "error": "Bạn cần đồng ý để hệ thống kiểm tra bài của bạn.",
            },
            current=current,
            status=400,
        )
    await auth.record_consent(db, current.user, settings)
    await db.commit()
    return RedirectResponse(safe_next(next), status_code=303)
