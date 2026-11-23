"""Môi trường Jinja2 và hàm render dùng chung cho mọi trang."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from citeproof.core.settings import Settings
from citeproof.web import labels
from citeproof.web.deps import CSRF_COOKIE, CurrentUser, anonymous_csrf_token

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["labels"] = labels
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status: int = 200,
    current: CurrentUser | None = None,
    nav: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    settings: Settings = request.app.state.settings
    current = current or getattr(request.state, "current", None)
    new_cookie = False
    if current is not None:
        csrf = current.web_session.csrf_token
    else:
        csrf, new_cookie = anonymous_csrf_token(request)
    ctx: dict[str, Any] = {
        "current": current,
        "csrf_token": csrf,
        "env": settings.env,
        "nav": nav,
        **(context or {}),
    }
    response = templates.TemplateResponse(request, name, ctx, status_code=status, headers=headers)
    if new_cookie:
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookies(),
            path="/",
        )
    return response
