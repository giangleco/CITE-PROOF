"""Smoke test E2E trên môi trường đang chạy (không chạy trong CI).

Đăng nhập, gửi một văn bản ngắn, chờ lần kiểm tra xong rồi mở trang báo cáo. Thoát 0 nếu đạt,
1 nếu không. Tài khoản phải có sẵn và đã đồng ý màn hình đồng ý (hoặc script đồng ý giúp).

    uv run python scripts/smoke.py --base-url https://DOMAIN --email E --password P
"""

import argparse
import re
import sys
import time

import httpx

_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
_RUN = re.compile(r"/runs/([0-9a-f-]{36})")
POLL_SECONDS = 3.0


def _token(response: httpx.Response) -> str:
    match = _CSRF.search(response.text)
    if match is None:
        raise RuntimeError(f"không thấy csrf_token ở {response.url.path}")
    return match.group(1)


def login(client: httpx.Client, email: str, password: str) -> None:
    token = _token(client.get("/login"))
    response = client.post(
        "/login", data={"email": email, "password": password, "csrf_token": token}
    )
    if response.status_code != 303:
        raise RuntimeError(f"đăng nhập thất bại: HTTP {response.status_code}")


def submit_text(client: httpx.Client) -> str:
    page = client.get("/documents/new?mode=text")
    if page.status_code == 303 and page.headers["location"].startswith("/consent"):
        consent = client.get("/consent")
        client.post("/consent", data={"csrf_token": _token(consent), "agree": "on"})
        page = client.get("/documents/new?mode=text")
    response = client.post(
        "/documents/text",
        data={
            "csrf_token": _token(page),
            "title": "Smoke test",
            "body": "Kiến trúc Transformer chỉ dựa vào cơ chế attention [1].",
            "references": "[1] A. Vaswani et al., Attention is all you need, 2017.",
        },
    )
    match = _RUN.search(response.headers.get("location", ""))
    if response.status_code != 303 or match is None:
        raise RuntimeError(f"gửi văn bản thất bại: HTTP {response.status_code}")
    return match.group(1)


def wait_finished(client: httpx.Client, run_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}/progress", headers={"HX-Request": "true"})
        if response.status_code != 200:
            raise RuntimeError(f"progress trả HTTP {response.status_code}")
        if f'href="/runs/{run_id}/report"' in response.text:
            return
        if f'hx-get="/runs/{run_id}/progress"' not in response.text:
            raise RuntimeError("lần kiểm tra dừng mà không hoàn tất")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"lần kiểm tra chưa xong sau {timeout:.0f} s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    try:
        with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
            login(client, args.email, args.password)
            run_id = submit_text(client)
            wait_finished(client, run_id, args.timeout)
            report = client.get(f"/runs/{run_id}/report")
            if report.status_code != 200:
                raise RuntimeError(f"báo cáo trả HTTP {report.status_code}")
    except (RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
