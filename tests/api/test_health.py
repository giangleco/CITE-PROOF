"""Test endpoint /api/v1/health."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport

from citeproof.api.app import create_app
from citeproof.core.settings import Settings


@asynccontextmanager
async def _client(url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=url))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        yield client


@pytest.mark.db
async def test_health_ok(database_url: str) -> None:
    async with _client(database_url) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_degraded_when_db_unreachable(caplog: pytest.LogCaptureFixture) -> None:
    url = "postgresql+psycopg://citeproof:secret-pw@localhost:1/citeproof"
    with caplog.at_level(logging.INFO):
        async with _client(url) as client:
            response = await client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
    assert "secret-pw" not in caplog.text
    assert "refused" not in caplog.text.lower()
