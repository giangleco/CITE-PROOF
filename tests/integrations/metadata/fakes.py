"""Helper cho test metadata: đọc fixture đã ghi, transport giả ghi lại request."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "metadata"

Handler = Callable[[httpx.Request], httpx.Response]


def load_fixture(name: str) -> Any:
    """JSON phản hồi thật đã ghi bằng `scripts/record_metadata_fixtures.py` (bản sao mới)."""
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def json_response(
    data: Any, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=data, headers=headers)


class FakeApi:
    """Handler cho `httpx.MockTransport`: ghi lại mọi request rồi trả lời bằng `handler`."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    @property
    def calls(self) -> int:
        return len(self.requests)


def sequence(*responses: httpx.Response | Exception) -> Handler:
    """Trả lời lần lượt theo thứ tự; hết danh sách thì lặp lại phần tử cuối."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return handler
