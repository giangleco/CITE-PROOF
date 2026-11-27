"""Cache phản hồi metadata dùng chung `metadata_cache` (spec §7.3, `08`, `07a` §4.7).

Khóa là `sha256` của JSON chuẩn hóa `{"op", "args"}`; truy vấn (sinh từ bài của người dùng)
không bao giờ được lưu, chỉ hash của nó. `response` chỉ gồm `{"records": [...]}` — các bản
ghi công khai đã dựng. TTL theo kết quả (`ok` 30 ngày, `not_found` 7 ngày); TTL `None` (DB
đánh giá) thì dòng không hết hạn. Lỗi không bao giờ được cache.
"""

import copy
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import MetadataOutcome
from citeproof.db.models import MetadataCacheEntry
from citeproof.integrations.metadata.models import WorkRecord
from citeproof.llm.cache import canonical_json

logger = logging.getLogger(__name__)


# Tăng khi đổi cách dựng/đọc bản ghi để các dòng cache cũ (có thể thiếu bản ghi do bị bỏ nhầm)
# không còn trúng. v2: nguồn trả toàn mục hỏng là `error`, không còn là `not_found` rỗng.
METADATA_CACHE_VERSION: int = 2


def query_hash(payload: Mapping[str, Any]) -> str:
    """Hash hex sha256 của `canonical_json({"v": phiên bản, **payload})` (khóa sắp xếp, NFC)."""
    versioned = {"v": METADATA_CACHE_VERSION, **payload}
    return hashlib.sha256(canonical_json(versioned).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedLookup:
    outcome: MetadataOutcome
    records: list[WorkRecord]


class LookupCache(Protocol):
    """Chỗ lưu kết quả tra cứu của `MetadataGateway` (PostgreSQL hoặc bộ nhớ cho test)."""

    async def get(self, source: str, payload: Mapping[str, Any]) -> CachedLookup | None: ...

    async def put(
        self,
        source: str,
        payload: Mapping[str, Any],
        outcome: MetadataOutcome,
        records: Sequence[WorkRecord],
    ) -> None: ...


def _response(records: Sequence[WorkRecord]) -> dict[str, Any]:
    return {"records": [record.model_dump(mode="json") for record in records]}


def _lookup(outcome: MetadataOutcome, response: Any) -> CachedLookup | None:
    if outcome is MetadataOutcome.ERROR:
        return None
    try:
        records = [WorkRecord.model_validate(item) for item in response["records"]]
    except (KeyError, TypeError, ValidationError):
        # Dòng cũ không đọc được (đổi lược đồ `WorkRecord`): coi như chưa có trong cache.
        logger.warning("metadata_cache_entry_invalid")
        return None
    return CachedLookup(outcome, records)


class MetadataCache:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ok_ttl: timedelta | None,
        not_found_ttl: timedelta | None,
    ) -> None:
        self._session_factory = session_factory
        self.ok_ttl = ok_ttl
        self.not_found_ttl = not_found_ttl

    async def get(self, source: str, payload: Mapping[str, Any]) -> CachedLookup | None:
        """Kết quả còn hạn của truy vấn; dòng đã hết hạn bị bỏ qua."""
        entry = MetadataCacheEntry
        stmt = select(entry.outcome, entry.response).where(
            entry.source == source,
            entry.query_hash == query_hash(payload),
            or_(entry.expires_at.is_(None), entry.expires_at > func.now()),
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _lookup(MetadataOutcome(row.outcome), row.response)

    async def put(
        self,
        source: str,
        payload: Mapping[str, Any],
        outcome: MetadataOutcome,
        records: Sequence[WorkRecord],
    ) -> None:
        """Ghi hoặc ghi đè kết quả (upsert); `ERROR` không được ghi."""
        if outcome is MetadataOutcome.ERROR:
            return
        ttl = self.ok_ttl if outcome is MetadataOutcome.OK else self.not_found_ttl
        # Cùng `now()` của giao dịch cho cả hai cột: `expires_at - fetched_at` đúng bằng TTL.
        now = func.now()
        stmt = insert(MetadataCacheEntry).values(
            source=source,
            query_hash=query_hash(payload),
            response=_response(records),
            outcome=outcome.value,
            fetched_at=now,
            expires_at=None if ttl is None else now + ttl,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MetadataCacheEntry.source, MetadataCacheEntry.query_hash],
            set_={
                "response": stmt.excluded.response,
                "outcome": stmt.excluded.outcome,
                "fetched_at": stmt.excluded.fetched_at,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        async with self._session_factory() as session:
            await session.execute(stmt)
            await session.commit()


class InMemoryMetadataCache:
    """Cache trong bộ nhớ, không TTL; cho test gateway không cần PostgreSQL."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[MetadataOutcome, dict[str, Any]]] = {}

    async def get(self, source: str, payload: Mapping[str, Any]) -> CachedLookup | None:
        entry = self._entries.get((source, query_hash(payload)))
        if entry is None:
            return None
        outcome, response = entry
        return _lookup(outcome, copy.deepcopy(response))

    async def put(
        self,
        source: str,
        payload: Mapping[str, Any],
        outcome: MetadataOutcome,
        records: Sequence[WorkRecord],
    ) -> None:
        if outcome is MetadataOutcome.ERROR:
            return
        self._entries[(source, query_hash(payload))] = (outcome, _response(records))
