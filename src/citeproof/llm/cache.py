"""Khoá cache xác định, cache store (memory / PostgreSQL) cho gọi LLM (07a §4.7.2-4.7.3)."""

import copy
import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.db.models.llm import LLMCacheEntry


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime phải có timezone để khoá cache không phụ thuộc máy chủ")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"khoá dict phải là str, nhận {type(key).__name__}")
            norm_key = unicodedata.normalize("NFC", key)
            if norm_key in result:
                raise ValueError(f"hai khoá trùng nhau sau chuẩn hoá NFC: {norm_key!r}")
            result[norm_key] = _normalize(item)
        return result
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    raise TypeError(f"không thể canonical hoá kiểu {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """JSON xác định: khoá sắp xếp, không khoảng trắng, mọi chuỗi chuẩn hoá NFC."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def worker_cache_key(
    *,
    namespace: str,
    provider: str,
    model: str,
    task: str,
    prompt_version: str,
    schema_version: str,
    inputs: Mapping[str, Any],
    attempt: int,
) -> str:
    return _digest(
        {
            "namespace": namespace,
            "provider": provider,
            "model": model,
            "task": task,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "inputs": inputs,
            "attempt": attempt,
        }
    )


def agent_step_cache_key(
    *,
    namespace: str,
    agent: str,
    agent_version: str,
    tool_catalog_version: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
) -> str:
    return _digest(
        {
            "namespace": namespace,
            "agent": agent,
            "agent_version": agent_version,
            "tool_catalog_version": tool_catalog_version,
            "model": model,
            "messages": messages,
        }
    )


def doc_namespace(document_id: UUID) -> str:
    return f"doc:{document_id}"


def eval_namespace(dataset: str, dataset_hash: str) -> str:
    return f"eval:{dataset}@{dataset_hash}"


@dataclass(frozen=True)
class CacheEntryMeta:
    namespace: str
    document_id: UUID | None
    task: str
    model: str
    prompt_version: str
    schema_version: str


def validate_namespace(namespace: str, document_id: UUID | None) -> None:
    """Hợp lệ: `doc:<uuid>` khớp document_id, hoặc `eval:<dataset>@<hash>` (không document_id)."""
    ns = namespace
    if ns.startswith("doc:"):
        part = ns[len("doc:") :]
        try:
            canonical = str(UUID(part))
        except ValueError:
            raise ValueError(f"namespace doc: có UUID không hợp lệ: {ns!r}") from None
        if canonical != part:
            raise ValueError(f"UUID trong namespace phải ở dạng chuẩn: {ns!r}")
        if document_id is None or str(document_id) != part:
            raise ValueError("namespace 'doc:' phải khớp document_id")
        return
    if ns.startswith("eval:"):
        dataset, sep, digest = ns[len("eval:") :].partition("@")
        invalid = (
            not sep
            or not dataset
            or not digest
            or any(c in x for x in (dataset, digest) for c in ":@")
        )
        if invalid:
            raise ValueError(f"namespace eval: không hợp lệ: {ns!r}")
        if document_id is not None:
            raise ValueError("namespace 'eval:' không được có document_id")
        return
    raise ValueError(f"namespace không hợp lệ: {ns!r}")


def _check_meta(meta: CacheEntryMeta) -> None:
    validate_namespace(meta.namespace, meta.document_id)


class CacheStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def put(self, key: str, meta: CacheEntryMeta, response: dict[str, Any]) -> None: ...

    async def delete(self, key: str) -> None: ...


class InMemoryCacheStore:
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        return copy.deepcopy(entry) if entry is not None else None

    async def put(self, key: str, meta: CacheEntryMeta, response: dict[str, Any]) -> None:
        _check_meta(meta)
        self._entries.setdefault(key, copy.deepcopy(response))

    async def delete(self, key: str) -> None:
        self._entries.pop(key, None)


class PgCacheStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(LLMCacheEntry)
                .where(LLMCacheEntry.cache_key == key)
                .values(hit_count=LLMCacheEntry.hit_count + 1)
                .returning(LLMCacheEntry.response)
            )
            row = result.first()
            await session.commit()
        if row is None:
            return None
        response: dict[str, Any] = row[0]
        return response

    async def put(self, key: str, meta: CacheEntryMeta, response: dict[str, Any]) -> None:
        _check_meta(meta)
        stmt = (
            insert(LLMCacheEntry)
            .values(
                cache_key=key,
                namespace=meta.namespace,
                document_id=meta.document_id,
                task=meta.task,
                model=meta.model,
                prompt_version=meta.prompt_version,
                schema_version=meta.schema_version,
                response=response,
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
        async with self._session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def delete(self, key: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(LLMCacheEntry).where(LLMCacheEntry.cache_key == key))
            await session.commit()
