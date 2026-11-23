"""Test endpoint /api/v1/health."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport

from citeproof.api.app import create_app
from citeproof.core.logging import configure_logging
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


async def test_health_degraded_when_db_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    url = "postgresql+psycopg://citeproof:secret-pw@localhost:1/citeproof"
    async with _client(url) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
    err = capsys.readouterr().err
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    degraded = [e for e in events if e["level"] == "WARNING" and "error_type" in e]
    assert degraded
    assert "secret-pw" not in err
    assert "refused" not in err.lower()
