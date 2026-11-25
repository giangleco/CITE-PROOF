"""Giới hạn số yêu cầu mỗi phút theo IP (T-608)."""

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from citeproof.core.settings import Settings
from citeproof.web.ratelimit import RateLimitMiddleware


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def build_app(
    clock: Clock, *, per_minute: int = 3, uploads: int = 10, proxy: bool = False
) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(database_url="postgresql+psycopg://x/x", env="local")

    @app.get("/login")
    async def login() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/static/app.css")
    async def static_css() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/api/v1/things")
    async def things() -> dict[str, str]:
        return {"ok": "1"}

    @app.post("/runs/{run_id}/missing/{entry_id}/upload")
    async def upload(run_id: str, entry_id: str) -> dict[str, str]:
        return {"ok": "1"}

    @app.post("/documents")
    async def documents() -> dict[str, str]:
        return {"ok": "1"}

    app.add_middleware(
        RateLimitMiddleware, per_minute=per_minute, uploads_per_minute=uploads, clock=clock
    )
    if proxy:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1"])
    return app


def client_of(app: FastAPI, ip: str = "10.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app, client=(ip, 1234)), base_url="http://t")


async def test_limit_returns_429_with_retry_after() -> None:
    async with client_of(build_app(Clock())) as c:
        for _ in range(3):
            assert (await c.get("/login")).status_code == 200
        r = await c.get("/login")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"
    assert "text/html" in r.headers["content-type"]


async def test_retry_after_is_time_to_window_end() -> None:
    clock = Clock()
    async with client_of(build_app(clock, per_minute=1)) as c:
        await c.get("/login")
        clock.now = 45.0
        r = await c.get("/login")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "15"


async def test_ips_independent() -> None:
    app = build_app(Clock(), per_minute=1)
    async with client_of(app, "10.0.0.1") as a, client_of(app, "10.0.0.2") as b:
        assert (await a.get("/login")).status_code == 200
        assert (await a.get("/login")).status_code == 429
        assert (await b.get("/login")).status_code == 200


async def test_static_and_health_exempt() -> None:
    async with client_of(build_app(Clock(), per_minute=1)) as c:
        for _ in range(5):
            assert (await c.get("/static/app.css")).status_code == 200
            assert (await c.get("/api/v1/health")).status_code == 200
        assert (await c.get("/login")).status_code == 200
        assert (await c.get("/login")).status_code == 429


async def test_window_resets() -> None:
    clock = Clock()
    async with client_of(build_app(clock, per_minute=1)) as c:
        assert (await c.get("/login")).status_code == 200
        assert (await c.get("/login")).status_code == 429
        clock.now = 60.0
        assert (await c.get("/login")).status_code == 200


async def test_upload_bucket_stricter() -> None:
    async with client_of(build_app(Clock(), per_minute=100, uploads=1)) as c:
        assert (await c.post("/runs/x/missing/y/upload")).status_code == 200
        r = await c.post("/runs/x/missing/y/upload")
        assert r.status_code == 429
        assert (await c.post("/documents")).status_code == 429
        for _ in range(3):
            assert (await c.get("/login")).status_code == 200


async def test_api_gets_json_error() -> None:
    async with client_of(build_app(Clock(), per_minute=1)) as c:
        await c.get("/api/v1/things")
        r = await c.get("/api/v1/things")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert r.headers["retry-after"] == "60"


async def test_expired_windows_pruned() -> None:
    clock = Clock()
    app = build_app(clock, per_minute=5)
    async with client_of(app, "10.0.0.1") as a, client_of(app, "10.0.0.2") as b:
        await a.get("/login")
        clock.now = 120.0
        await b.get("/login")
    limiter = app.middleware_stack  # outermost wrapper chain; walk to our middleware
    while limiter is not None and not isinstance(limiter, RateLimitMiddleware):
        limiter = getattr(limiter, "app", None)
    assert isinstance(limiter, RateLimitMiddleware)
    assert len(limiter.windows) == 1


async def test_clients_behind_trusted_proxy_get_independent_buckets() -> None:
    app = build_app(Clock(), per_minute=1, proxy=True)
    async with httpx.AsyncClient(
        transport=ASGITransport(app, client=("127.0.0.1", 1)), base_url="http://t"
    ) as c:
        one = {"X-Forwarded-For": "1.1.1.1"}
        two = {"X-Forwarded-For": "2.2.2.2"}
        assert (await c.get("/login", headers=one)).status_code == 200
        assert (await c.get("/login", headers=one)).status_code == 429
        assert (await c.get("/login", headers=two)).status_code == 200


@pytest.mark.db
async def test_mount_web_applies_settings_limit(web_settings: Settings, clean_db: None) -> None:
    from tests.web.conftest import client_for

    tight = web_settings.model_copy(update={"rate_limit_per_minute": 2})
    async with client_for(tight) as c:
        codes = [(await c.get("/login")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
