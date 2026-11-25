"""Khung công cụ cho agent (07a §4.6.1, ADR-010): công cụ chỉ đọc, lỗi có `fix`, submit.

Công cụ được dựng theo phiên với phạm vi khóa bằng closure (ví dụ A2 nhận `source_file_id` lúc
khởi tạo), nên đối số do model gửi không đổi được phạm vi. Lỗi dự kiến (ID lạ, truy vấn quá
ngắn) trả về `ToolError` để model tự sửa; ngoại lệ là lỗi thật và kết thúc lần chạy agent.
Mô tả công cụ nằm trong `prompts/tools/<catalog>/<version>.yaml` (tiếng Anh, có phiên bản;
phiên bản catalog nằm trong khóa cache từng bước).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel

from citeproof.llm.types import OutputModel


@dataclass(frozen=True)
class ToolError:
    """Lỗi trả cho model: `{"error": {"code", "message", "fix"}}`, `fix` nói cần làm gì khác."""

    code: str
    message: str
    fix: str


@dataclass(frozen=True)
class ToolResult:
    """Kết quả công cụ dạng chuỗi (thường là JSON); runner cắt khi quá dài."""

    content: str


class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError: ...


@dataclass(frozen=True)
class SubmitTool[T: OutputModel]:
    """Công cụ nộp kết quả: `model` là schema strict, `check` là kiểm tra nghiệp vụ tất định.

    `check` trả danh sách rỗng khi hợp lệ; lỗi được gửi lại model kèm `fix` (tối đa
    `AgentBudget.max_submit_attempts` lần nộp sai, sau đó runner nhận bản cuối).
    """

    name: str
    description: str
    model: type[T]
    check: Callable[[T], Sequence[ToolError]]


def load_tool_catalog(root: Path, catalog: str, version: str) -> dict[str, str]:
    """Đọc `root/tools/<catalog>/<version>.yaml` dạng `name: description`.

    Mô tả được cắt khoảng trắng hai đầu; file sai dạng (không phải mapping chuỗi → chuỗi
    khác rỗng) là `ValueError`.
    """
    path = root / "tools" / catalog / f"{version}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"tool catalog not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path}: expected a non-empty mapping of tool name to description")
    catalog_map: dict[str, str] = {}
    for name, description in data.items():
        if not isinstance(name, str) or not name or not isinstance(description, str):
            raise ValueError(f"{path}: every entry must be 'name: description' (strings)")
        text = description.strip()
        if not text:
            raise ValueError(f"{path}: empty description for {name!r}")
        catalog_map[name] = text
    return catalog_map
