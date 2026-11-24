"""Ghi `works` từ bản ghi metadata đã khớp (spec §7.3, QĐ-06; `08` §4.2).

Tìm dòng sẵn có theo DOI → OpenAlex ID → Semantic Scholar ID → arXiv ID (không phiên bản),
dừng ở định danh đầu tiên khớp. Dòng `metadata_source = "sample"` (bài mẫu của giao diện)
hoặc cũ hơn TTL bị ghi đè bằng bản ghi mới (trường tùy chọn mà bản ghi mới thiếu giữ giá trị
cũ); dòng thật còn mới giữ nguyên, chỉ được lấp các cột còn trống (định danh, abstract, lĩnh
vực OpenAlex…). DOI lưu dạng chuẩn hóa (chữ thường, `08` §4.4).

Hai giao dịch cùng ghi một tài liệu (hai mục trích cùng bài, hai lần chạy song song) được xếp
hàng bằng advisory lock theo từng định danh, khóa theo thứ tự cố định để không deadlock.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from citeproof.core.domain import normalize_doi
from citeproof.db.models import Work
from citeproof.integrations.metadata import WorkRecord
from citeproof.modules.references.normalize import title_norm

# Trùng `services.sample.SAMPLE_METADATA_SOURCE` (module nghiệp vụ không import `services`).
SAMPLE_METADATA_SOURCE = "sample"

_IDENTITY_COLUMNS: Mapping[str, InstrumentedAttribute[str | None]] = {
    "doi": Work.doi,
    "openalex_id": Work.openalex_id,
    "s2_id": Work.s2_id,
    "arxiv_id": Work.arxiv_id,
}
# Cột lấp từ bản ghi mới khi đang trống; khi ghi đè, bản ghi mới thiếu thì giữ giá trị cũ.
_OPTIONAL = ("doi", "openalex_id", "s2_id", "arxiv_id", "isbn", "abstract", "language", "field")


def _values(record: WorkRecord) -> dict[str, Any]:
    return {
        "doi": normalize_doi(record.doi),
        "openalex_id": record.openalex_id,
        "s2_id": record.s2_id,
        "arxiv_id": record.arxiv_id,
        "isbn": record.isbn,
        "title": record.title,
        "title_norm": title_norm(record.title),
        "authors": [author.model_dump(mode="json") for author in record.authors],
        "year": record.year,
        "venue": record.venue,
        "abstract": record.abstract,
        "language": record.language,
        "field": record.field,
        "metadata_source": record.source,
    }


async def _lock_identities(session: AsyncSession, identities: Mapping[str, str]) -> None:
    for key in sorted(f"{name}:{value}" for name, value in identities.items()):
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def _find(
    session: AsyncSession, identities: Mapping[str, str], ttl: timedelta
) -> tuple[UUID, str, bool] | None:
    """(id, nguồn metadata, đã quá TTL) của dòng khớp định danh đầu tiên theo thứ tự."""
    stale = (Work.retrieved_at < func.now() - ttl).label("stale")
    for name, value in identities.items():
        row = (
            await session.execute(
                select(Work.id, Work.metadata_source, stale)
                .where(_IDENTITY_COLUMNS[name] == value)
                .order_by(Work.retrieved_at.desc(), Work.id)
                .limit(1)
            )
        ).one_or_none()
        if row is not None:
            return row.id, row.metadata_source, bool(row.stale)
    return None


async def upsert_work(session: AsyncSession, record: WorkRecord, *, ttl: timedelta) -> UUID:
    """ID dòng `works` của `record` (tạo, ghi đè hoặc lấp chỗ trống); không commit."""
    values = _values(record)
    identities = {name: values[name] for name in _IDENTITY_COLUMNS if values[name]}
    await _lock_identities(session, identities)
    found = await _find(session, identities, ttl)
    if found is None:
        work_id: UUID = (
            await session.execute(insert(Work).values(**values).returning(Work.id))
        ).scalar_one()
        return work_id
    work_id, source, stale = found
    if source == SAMPLE_METADATA_SOURCE or stale:
        refreshed = {
            key: value for key, value in values.items() if key not in _OPTIONAL or value is not None
        }
        await session.execute(
            update(Work)
            .where(Work.id == work_id)
            .values(**refreshed, retrieved_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return work_id
    filled = {
        key: func.coalesce(getattr(Work, key), values[key])
        for key in _OPTIONAL
        if values[key] is not None
    }
    if filled:
        await session.execute(
            update(Work)
            .where(Work.id == work_id)
            .values(**filled)
            .execution_options(synchronize_session=False)
        )
    return work_id
