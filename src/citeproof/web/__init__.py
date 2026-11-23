"""Giao diện web render phía server (ADR-005): gắn static, middleware, lỗi, router."""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from citeproof.core.errors import (
    AuthError,
    CiteProofError,
    ConflictError,
    InputError,
    NotFoundError,
    RateLimitedError,
)
from citeproof.core.settings import Settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
_FORM_OVERHEAD_BYTES = 1_048_576

_ERROR_PAGES: dict[int, tuple[str, str]] = {
    400: ("Yêu cầu không hợp lệ", "Dữ liệu gửi lên không đúng. Quay lại trang trước và thử lại."),
    401: ("Cần đăng nhập", "Bạn cần đăng nhập để tiếp tục."),
    403: (
        "Biểu mẫu đã hết hạn",
        "Phiên làm việc đã hết hạn hoặc biểu mẫu không hợp lệ. Tải lại trang rồi thử lại.",
    ),
    404: ("Không tìm thấy trang", "Trang bạn mở không có, hoặc bạn không có quyền xem."),
    409: (
        "Đang có lần kiểm tra chạy",
        "Bài này đang được kiểm tra. Chờ lần kiểm tra xong rồi thử lại.",
    ),
    413: (
        "Tệp quá lớn",
        "Tệp hoặc nội dung gửi lên vượt quá giới hạn cho phép. Hãy chọn tệp nhỏ hơn.",
    ),
    429: ("Vượt giới hạn", "Bạn đã thao tác quá nhiều lần. Thử lại sau ít phút."),
    500: (
        "Lỗi hệ thống",
        "Đã có lỗi phía máy chủ. Thử lại sau; nếu lỗi lặp lại, báo cho nhóm phát triển.",
    ),
}
_STATUS_BY_ERROR: tuple[tuple[type[CiteProofError], int], ...] = (
    (NotFoundError, 404),
    (RateLimitedError, 429),
    (ConflictError, 409),
    (AuthError, 401),
    (InputError, 400),
)


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _api_code(status: int) -> str:
    return {400: "INVALID_INPUT", 404: "NOT_FOUND", 413: "FILE_TOO_LARGE", 429: "RATE_LIMITED"}.get(
        status, "INTERNAL_ERROR" if status >= 500 else "ERROR"
    )


def error_response(request: Request, status: int, message: str | None = None) -> Response:
    title, default = _ERROR_PAGES.get(status, _ERROR_PAGES[500])
    if _is_api(request):
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": _api_code(status), "message": message or default, "details": {}}
            },
        )
    from citeproof.web.rendering import render

    return render(
        request, "pages/error.html", {"title": title, "message": message or default}, status=status
    )


def _redirect(request: Request, target: str) -> Response:
    from citeproof.web.deps import is_htmx

    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": target.split("?")[0]})
    return RedirectResponse(target, status_code=303)


def _install_handlers(app: FastAPI) -> None:
    from citeproof.web.deps import ConsentRequired, CsrfError, LoginRequired

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired) -> Response:
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return _redirect(request, f"/login?next={quote(path, safe='')}")

    @app.exception_handler(ConsentRequired)
    async def consent_required(request: Request, exc: ConsentRequired) -> Response:
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return _redirect(request, f"/consent?next={quote(path, safe='')}")

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, exc: CsrfError) -> Response:
        return error_response(request, 403)

    @app.exception_handler(CiteProofError)
    async def domain_error(request: Request, exc: CiteProofError) -> Response:
        for cls, status in _STATUS_BY_ERROR:
            if isinstance(exc, cls):
                message = str(exc) or None if status != 404 else None
                return error_response(request, status, message)
        logger.error("unhandled_domain_error", extra={"error_type": type(exc).__name__})
        return error_response(request, 500)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        return error_response(request, exc.status_code if exc.status_code in _ERROR_PAGES else 500)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        return error_response(request, 400)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> Response:
        logger.error("unhandled_error", extra={"error_type": type(exc).__name__})
        return error_response(request, 500)


def mount_web(app: FastAPI, settings: Settings) -> None:
    from citeproof.web.routes import ROUTERS
    from citeproof.web.security import BodyLimitMiddleware, SecurityHeadersMiddleware

    async def too_large(scope: Scope, receive: Receive, send: Send) -> None:
        response = error_response(Request(scope, receive), 413)
        await response(scope, receive, send)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    _install_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=settings.max_upload_bytes + _FORM_OVERHEAD_BYTES,
        too_large=too_large,
    )
    app.add_middleware(SecurityHeadersMiddleware)
