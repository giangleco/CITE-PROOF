"""Môi trường Jinja2 và hàm render dùng chung cho mọi trang."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from citeproof.core.settings import Settings
from citeproof.services.documents import ADMIN_VARIANTS
from citeproof.web import labels
from citeproof.web.deps import CSRF_COOKIE, CurrentUser, anonymous_csrf_token

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["labels"] = labels
# Biến thể admin chọn được (tạo bài mới và kiểm tra lại dùng chung một danh sách).
templates.env.globals["admin_variants"] = [v.value for v in ADMIN_VARIANTS]


def highlight_claim(sentence: str, span: str, marker: str) -> Markup:
    """Câu của người dùng với phần nhận định được tô và marker được đánh dấu; mọi phần
    đều được escape trước khi bọc thẻ."""

    def mark(text: str) -> Markup:
        if marker and marker in text:
            before, _, after = text.partition(marker)
            return (
                escape(before)
                + Markup('<span class="marker">')
                + escape(marker)
                + (Markup("</span>") + escape(after))
            )
        return escape(text)

    if span and span in sentence:
        before, _, after = sentence.partition(span)
        return (
            mark(before)
            + Markup('<span class="span">')
            + escape(span)
            + (Markup("</span>") + mark(after))
        )
    return mark(sentence)


templates.env.filters["highlight_claim"] = highlight_claim

VN_TZ = timezone(timedelta(hours=7))  # Việt Nam không có giờ mùa hè.


def vn_time(value: datetime | None, fmt: str = "%d/%m/%Y") -> str:
    if value is None:
        return "[chưa có]"
    return value.astimezone(VN_TZ).strftime(fmt)


templates.env.filters["vn_time"] = vn_time
templates.env.filters["source_names"] = lambda codes: ", ".join(
    labels.source_name(c) for c in codes
)
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
