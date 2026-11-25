"""Kiểu dùng chung cho LLMClient và AgentRunner (07a §4.4.2, §4.5.1).

Không có tham số sampling hay tool choice.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from citeproof.core.errors import CiteProofError


class OutputModel(BaseModel):
    """Lớp cơ sở cho mọi schema đầu ra của LLM."""

    model_config = ConfigDict(extra="forbid")
    SCHEMA_VERSION: ClassVar[str]


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    system: str
    messages: tuple[Mapping[str, str], ...]
    output_schema: Mapping[str, Any]
    max_tokens: int
    # None: provider không gửi effort (model dùng mặc định của nó).
    effort: str | None = None


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    # Model thực sự trả lời (có thể là model dự phòng); rỗng nghĩa là model đã yêu cầu.
    model: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """Công cụ gửi cho model: `input_schema` là JSON schema thô (provider làm strict)."""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class TurnRequest:
    """Một lượt agent: hội thoại gồm các khối nội dung của API, chỉ được nối thêm."""

    model: str
    system: str
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[ToolSpec, ...]
    max_tokens: int
    effort: str | None


@dataclass(frozen=True)
class TurnResponse:
    # Khối nội dung (text, thinking, tool_use) đúng như API trả, để gửi lại nguyên vẹn.
    content: tuple[Mapping[str, Any], ...]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    # Model thực sự trả lời.
    model: str


class CacheMode(StrEnum):
    READ_WRITE = "read_write"
    REPLAY_ONLY = "replay_only"
    FRESH = "fresh"


@dataclass(frozen=True)
class CallContext:
    namespace: str
    document_id: UUID | None
    run_id: UUID | None
    stage: str | None


@dataclass(frozen=True)
class LLMResult[T: OutputModel]:
    value: T
    cache_hit: bool
    cache_key: str
    input_tokens: int
    output_tokens: int
    # Model thực sự trả lời; với cache hit là model đã ghi cùng entry.
    model: str = ""


class CacheMissError(CiteProofError):
    """REPLAY_ONLY nhưng không có entry trong cache."""

    def __init__(self, cache_key: str) -> None:
        super().__init__(f"cache miss ở chế độ replay_only: {cache_key}")
        self.cache_key = cache_key


class CacheEntryInvalidError(CiteProofError):
    """REPLAY_ONLY nhưng entry cache hỏng (thiếu value hoặc sai schema)."""

    def __init__(self, cache_key: str) -> None:
        super().__init__(f"entry cache không hợp lệ: {cache_key}")
        self.cache_key = cache_key
