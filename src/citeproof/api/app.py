"""FastAPI app: endpoint health và vòng đời engine."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from citeproof.core.settings import Settings, get_settings
from citeproof.db.session import make_engine

logger = logging.getLogger(__name__)

HEALTH_CONNECT_TIMEOUT_SECONDS = 2


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(resolved.database_url, connect_timeout=HEALTH_CONNECT_TIMEOUT_SECONDS)
        app.state.engine = engine
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="CiteProof", lifespan=lifespan)

    @app.get("/api/v1/health")
    async def health(request: Request) -> JSONResponse:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            # Chỉ log loại lỗi, không log nội dung (có thể chứa DSN/mật khẩu).
            logger.warning(
                "health check: database unavailable",
                extra={"error_type": type(exc).__name__},
            )
            return JSONResponse(
                status_code=503, content={"status": "degraded", "database": "unavailable"}
            )
        return JSONResponse(content={"status": "ok", "database": "ok"})

    return app
