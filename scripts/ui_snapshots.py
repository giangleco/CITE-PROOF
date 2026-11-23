"""Chụp màn hình các trang chính của giao diện web để kiểm tra bằng mắt (không chạy trong CI).

Đăng nhập bằng httpx, rồi điều khiển Chrome không giao diện qua DevTools Protocol, đặt
cookie phiên và chụp mỗi trang ở hai độ rộng (1360 px và 390 px). Trang được tải từ chính
máy chủ nên CSP, font và HTMX hoạt động như với người dùng thật. Script cũng báo trang
nào bị tràn ngang.

    uv run python scripts/ui_snapshots.py --email E --password P --out DIR
"""

import argparse
import asyncio
import base64
import itertools
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

WIDTHS = ((1360, 1000, "desktop"), (390, 844, "mobile"))
_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
_RUN = re.compile(r'href="/runs/([0-9a-f-]{36})/report"')
_CLAIM = re.compile(r'hx-get="/runs/[0-9a-f-]{36}/claims/([0-9a-f-]{36})"')


class Cdp:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.events: list[dict[str, Any]] = []

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        msg_id = next(self.ids)
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == msg_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                result: dict[str, Any] = message.get("result", {})
                return result
            self.events.append(message)

    async def wait_event(self, name: str, limit_s: float = 15) -> None:
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            if any(e.get("method") == name for e in self.events):
                self.events.clear()
                return
            try:
                message = json.loads(await asyncio.wait_for(self.ws.recv(), 1))
            except TimeoutError:
                continue
            self.events.append(message)
        raise TimeoutError(name)


async def login(base: str, email: str, password: str) -> tuple[httpx.AsyncClient, str]:
    client = httpx.AsyncClient(base_url=base)
    page = await client.get("/login")
    token = _CSRF.search(page.text)
    assert token, "không thấy csrf_token ở trang đăng nhập"
    response = await client.post(
        "/login", data={"email": email, "password": password, "csrf_token": token.group(1)}
    )
    assert response.status_code == 303, f"đăng nhập thất bại: {response.status_code}"
    probe = await client.get("/documents/new")
    if probe.status_code == 303 and probe.headers["location"].startswith("/consent"):
        consent = await client.get("/consent")
        token = _CSRF.search(consent.text)
        assert token
        await client.post("/consent", data={"csrf_token": token.group(1), "agree": "on"})
    return client, client.cookies["cp_session"]


async def demo_run(client: httpx.AsyncClient) -> str:
    page = await client.get("/documents/new?mode=text")
    token = _CSRF.search(page.text)
    assert token
    response = await client.post(
        "/documents/text",
        data={
            "csrf_token": token.group(1),
            "title": "Chương 2: Tổng quan tài liệu",
            "body": "Kiến trúc Transformer chỉ dựa vào cơ chế attention [1].",
            "references": "[1] A. Vaswani et al., Attention is all you need, 2017.",
            "demo_delay": "3",
            "demo_fail_at": "S5",
        },
    )
    return response.headers["location"]


async def shoot(cdp: Cdp, base: str, path: str, out: Path, name: str, overflow: list[str]) -> None:
    for width, height, label in WIDTHS:
        await cdp.call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=width < 500,
        )
        await cdp.call("Page.navigate", url=base + path)
        await cdp.wait_event("Page.loadEventFired")
        await asyncio.sleep(0.6)
        check = await cdp.call(
            "Runtime.evaluate",
            expression="document.documentElement.scrollWidth > window.innerWidth",
            returnByValue=True,
        )
        if check["result"].get("value"):
            overflow.append(f"{name}-{label}")
        metrics = await cdp.call("Page.getLayoutMetrics")
        full = metrics["cssContentSize"]
        shot = await cdp.call(
            "Page.captureScreenshot",
            format="png",
            captureBeyondViewport=True,
            clip={"x": 0, "y": 0, "width": width, "height": min(full["height"], 4000), "scale": 1},
        )
        (out / f"{name}-{label}.png").write_bytes(base64.b64decode(shot["data"]))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--chrome", default=shutil.which("google-chrome") or "chromium")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    client, session_token = await login(args.base, args.email, args.password)
    listing = (await client.get("/documents")).text
    run_match = _RUN.search(listing)
    assert run_match, "chưa có bài nào đã kiểm tra xong; chạy seed-sample trước"
    run_id = run_match.group(1)
    claims = (await client.get(f"/runs/{run_id}/claims?status=CONTRADICTED")).text
    claim_match = _CLAIM.search(claims)
    claim_qs = f"?c={claim_match.group(1)}" if claim_match else ""
    demo_path = await demo_run(client)

    profile = tempfile.mkdtemp(prefix="cp-chrome-")
    chrome = await asyncio.create_subprocess_exec(
        args.chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        "--remote-debugging-port=9333",
        f"--user-data-dir={profile}",
        "about:blank",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    overflow: list[str] = []
    try:
        targets: list[dict[str, Any]] = []
        async with httpx.AsyncClient() as devtools:
            for _ in range(50):
                try:
                    targets = (await devtools.get("http://127.0.0.1:9333/json/list")).json()
                    if any(t.get("type") == "page" for t in targets):
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        page = next(t for t in targets if t.get("type") == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = Cdp(ws)
            await cdp.call("Page.enable")
            await shoot(cdp, args.base, "/login", args.out, "01-login", overflow)
            host = httpx.URL(args.base).host
            await cdp.call("Network.enable")
            await cdp.call(
                "Network.setCookie", name="cp_session", value=session_token, domain=host, path="/"
            )
            pages = [
                ("02-documents", "/documents"),
                ("03-new-file", "/documents/new"),
                ("04-new-text", "/documents/new?mode=text"),
                ("05-progress-running", demo_path),
                ("06-report-overview", f"/runs/{run_id}/report"),
                ("07-report-references", f"/runs/{run_id}/references"),
                ("08-report-claims", f"/runs/{run_id}/claims{claim_qs}"),
                ("09-report-missing", f"/runs/{run_id}/missing"),
                ("10-account", "/account"),
                ("11-consent", "/consent"),
            ]
            for name, path in pages:
                await shoot(cdp, args.base, path, args.out, name, overflow)
            await asyncio.sleep(12)
            await shoot(cdp, args.base, demo_path, args.out, "12-progress-failed", overflow)
    finally:
        chrome.terminate()
        await chrome.wait()
        shutil.rmtree(profile, ignore_errors=True)
        await client.aclose()
    print(f"Đã chụp vào {args.out}")
    print("Tràn ngang: " + (", ".join(overflow) if overflow else "không có"))


if __name__ == "__main__":
    asyncio.run(main())
