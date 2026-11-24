"""S3 — kiểm tra tài liệu tham khảo (spec §7.3, §6.4; T-301 đến T-304, QĐ-19, QĐ-41).

Đơn vị là một `reference_entries` trong phạm vi lần chạy (`RunInfo.in_scope`); mục đã có
`reference_checks` của lần chạy bị bỏ qua (tiếp tục sau lỗi). Mỗi mục, song song tối đa
`reference_concurrency`:

1. Chuỗi tra cứu (`CascadeResolver`) qua gateway của lần chạy (ngắt mạch theo lần chạy),
   rồi bước leo thang tùy chọn (A1, Task 25).
2. Tài liệu `WEB` có URL: kiểm tra URL qua `SafeHttpClient`; tra archive.org khi
   `web_archive_lookup` bật.
3. Ghi `works` (nếu khớp) và `reference_checks` trong một giao dịch rào lease ngắn. Mọi lời
   gọi mạng xong trước giao dịch đó.

Lỗi của nguồn metadata đã thành nguồn lỗi trong gateway. Lỗi nghiệp vụ khác của một mục
(`CiteProofError`) được cô lập: lỗi ở chuỗi tra cứu → `LOOKUP_FAILED`; lỗi ở bước leo thang
→ giữ kết quả chuỗi tra cứu và gắn cờ. `InternalError`, lỗi cache phát lại, mất lease và lỗi
không phải `CiteProofError` (DB, lỗi lập trình) làm stage lỗi (preflight F12, F13).

Xong mọi mục (lần chạy không có phạm vi): `documents.domain_flag` theo lĩnh vực OpenAlex của
các tài liệu đã khớp và cảnh báo `OUT_OF_DOMAIN` trong `parse_warnings` (QĐ-41). Lần chạy
con chỉ tra một phần nên không đổi cờ này. Không log chuỗi tài liệu tham khảo, truy vấn
hay URL.
"""

import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ReferenceStatus, RefType, ResolvedBy, StageName
from citeproof.core.errors import CiteProofError, InternalError, LeaseLostError
from citeproof.db.models import Document, ReferenceCheck, ReferenceEntry, Work
from citeproof.llm.types import CacheEntryInvalidError, CacheMissError
from citeproof.modules.references.cascade import (
    CascadeResolver,
    CascadeResult,
    ReferenceEntryView,
    ReferenceEscalation,
)
from citeproof.modules.references.domain import domain_flag, with_domain_warning
from citeproof.modules.references.web_check import archive_lookup, check_url
from citeproof.modules.references.works import upsert_work
from citeproof.pipeline.config import RunInfo, load_run_info
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.pipeline.units import process_units

logger = logging.getLogger(__name__)

# Lỗi không bao giờ bị cô lập thành kết quả của một mục (preflight F12, F13).
_NOT_ISOLATED = (InternalError, CacheMissError, CacheEntryInvalidError, LeaseLostError)
_MATCHED = (ReferenceStatus.MATCHED.value, ReferenceStatus.METADATA_MISMATCH.value)
_SCORE_DIGITS = 4


@dataclass(frozen=True)
class _Outcome:
    result: CascadeResult
    resolved_by: ResolvedBy = ResolvedBy.CASCADE
    agent_run_id: UUID | None = None
    url_alive: bool | None = None
    archive_url: str | None = None


def _isolated(error: CiteProofError) -> bool:
    return not isinstance(error, _NOT_ISOLATED)


def _lookup_failed() -> CascadeResult:
    return CascadeResult(
        status=ReferenceStatus.LOOKUP_FAILED,
        record=None,
        match=None,
        sources_searched=(),
        sources_failed=(),
        needs_user_review=False,
        close_call=False,
    )


def _honest(result: CascadeResult) -> CascadeResult:
    """ "Chưa tìm thấy" chỉ khi có nguồn đã tra thành công; ngược lại `LOOKUP_FAILED`."""
    if result.status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES and not (
        result.sources_searched
    ):
        return replace(result, status=ReferenceStatus.LOOKUP_FAILED)
    return result


def _score(result: CascadeResult) -> Decimal | None:
    if result.match is None:
        return None
    return Decimal(str(round(result.match.score, _SCORE_DIGITS)))


class CheckReferencesStage:
    name: StageName = StageName.S3

    def __init__(
        self, deps: PipelineDeps, *, escalation: ReferenceEscalation | None = None
    ) -> None:
        self._deps = deps
        self._escalation = escalation

    async def run(self, ctx: StageContext) -> StageResult:
        if self._deps.metadata is None:
            raise InternalError("S3 cần gateway metadata (PipelineDeps.metadata chưa được đặt).")
        async with self._deps.session_factory() as session:
            info = await load_run_info(session, ctx.run_id)
            entries = await _entries(session, info)
            done = await _done(session, ctx.run_id)
        resolver = CascadeResolver(self._deps.metadata())

        async def handle(entry: ReferenceEntryView) -> None:
            outcome = await self._check(resolver, entry, info)
            await self._write(ctx, entry, outcome)

        report = await process_units(
            entries,
            key=lambda entry: str(entry.id),
            done_keys=done,
            handle=handle,
            concurrency=self._deps.settings.reference_concurrency,
        )
        if report.failed:
            # Chỉ lỗi không được cô lập lọt tới đây: stage lỗi; mục đã ghi được giữ.
            raise report.failed[0][1]
        if info.config.scope is None:
            await self._write_domain(ctx, info.document_id)
        return StageResult()

    async def _check(
        self, resolver: CascadeResolver, entry: ReferenceEntryView, info: RunInfo
    ) -> _Outcome:
        try:
            result = await resolver.resolve(entry, None)
        except CiteProofError as exc:
            if not _isolated(exc):
                raise
            logger.warning("reference_lookup_failed", extra={"error_type": type(exc).__name__})
            result = _lookup_failed()
        outcome = await self._escalate(entry, result, info)
        url = entry.parsed.url
        if entry.ref_type is RefType.WEB and url:
            safe_http = self._deps.safe_http
            alive = await check_url(safe_http, url)
            archived = (
                await archive_lookup(safe_http, url)
                if self._deps.settings.web_archive_lookup
                else None
            )
            outcome = replace(outcome, url_alive=alive, archive_url=archived)
        return outcome

    async def _escalate(
        self, entry: ReferenceEntryView, result: CascadeResult, info: RunInfo
    ) -> _Outcome:
        if self._escalation is None:
            return _Outcome(result)
        try:
            escalated = await self._escalation.maybe_resolve(entry, result, info)
        except CiteProofError as exc:
            if not _isolated(exc):
                raise
            logger.warning("reference_escalation_failed", extra={"error_type": type(exc).__name__})
            return _Outcome(replace(result, needs_user_review=True))
        if escalated is None:
            return _Outcome(result)
        return _Outcome(
            escalated.result,
            resolved_by=escalated.resolved_by,
            agent_run_id=escalated.agent_run_id,
        )

    async def _write(self, ctx: StageContext, entry: ReferenceEntryView, outcome: _Outcome) -> None:
        result = _honest(outcome.result)
        ttl = timedelta(days=self._deps.settings.metadata_cache_ttl_days)
        mismatches = (
            [m.model_dump(mode="json") for m in result.match.mismatches] if result.match else []
        )
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            work_id = (
                await upsert_work(session, result.record, ttl=ttl)
                if result.record is not None
                else None
            )
            await session.execute(
                insert(ReferenceCheck)
                .values(
                    run_id=ctx.run_id,
                    reference_entry_id=entry.id,
                    status=result.status.value,
                    work_id=work_id,
                    mismatches=mismatches,
                    sources_searched=list(result.sources_searched),
                    sources_failed=list(result.sources_failed),
                    resolved_by=outcome.resolved_by.value,
                    agent_run_id=outcome.agent_run_id,
                    match_score=_score(result),
                    needs_user_review=result.needs_user_review,
                    url_alive=outcome.url_alive,
                    archive_url=outcome.archive_url,
                )
                .on_conflict_do_nothing(index_elements=["run_id", "reference_entry_id"])
            )

    async def _write_domain(self, ctx: StageContext, document_id: UUID) -> None:
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            fields = (
                await session.execute(
                    select(Work.field)
                    .join(ReferenceCheck, ReferenceCheck.work_id == Work.id)
                    .where(ReferenceCheck.run_id == ctx.run_id, ReferenceCheck.status.in_(_MATCHED))
                )
            ).scalars()
            flag = domain_flag(list(fields))
            warnings: list[Any] = (
                await session.scalar(
                    select(Document.parse_warnings)
                    .where(Document.id == document_id)
                    .with_for_update()
                )
                or []
            )
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(domain_flag=flag.value, parse_warnings=with_domain_warning(warnings, flag))
            )


async def _entries(session: AsyncSession, info: RunInfo) -> list[ReferenceEntryView]:
    rows = (
        await session.execute(
            select(ReferenceEntry)
            .where(ReferenceEntry.document_id == info.document_id)
            .order_by(ReferenceEntry.order_index)
        )
    ).scalars()
    return [
        ReferenceEntryView(
            id=row.id,
            raw_text=row.raw_text,
            parsed=ParsedReference.model_validate(row.parsed or {}),
            ref_type=RefType(row.ref_type),
        )
        for row in rows
        if info.in_scope(row.id)
    ]


async def _done(session: AsyncSession, run_id: UUID) -> set[str]:
    ids = (
        await session.execute(
            select(ReferenceCheck.reference_entry_id).where(ReferenceCheck.run_id == run_id)
        )
    ).scalars()
    return {str(entry_id) for entry_id in ids}
