"""Kiểu dùng chung cho LLMClient (07a §4.4.2). Không có tham số sampling hay tool choice."""

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


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str


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


class CacheMissError(CiteProofError):
    """REPLAY_ONLY nhưng không có entry trong cache."""

    def __init__(self, cache_key: str) -> None:
        super().__init__(f"cache miss ở chế độ replay_only: {cache_key}")
        self.cache_key = cache_key
