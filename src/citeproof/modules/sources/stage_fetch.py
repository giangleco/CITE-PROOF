"""S5 — lấy bản mở cho các work của lần chạy (spec §7.5, §6.4; T-403, QĐ-07, QĐ-17, QĐ-18).

Đơn vị là một work mà `reference_checks` trong phạm vi lần chạy trỏ tới
(`selection.run_work_ids`). Mỗi work, song song tối đa `WORK_CONCURRENCY`:

1. Abstract: `works.abstract` thành JSON `{"title", "abstract"}` ở
   `sources/open/<sha256>.abstract.json` (`ABSTRACT`, `OPEN_ABSTRACT`) khi work chưa có, để
   bằng chứng trên abstract luôn có chunk để trỏ tới (QĐ-17).
2. Toàn văn, khi work chưa có: thử các nguồn theo thứ tự của `integrations.oa.make_resolvers`
   (arXiv, ACL Anthology, OpenAlex, Unpaywall khi có email liên hệ), tải từng lựa chọn qua
   `deps.source_http` và dừng ở bản PDF đầu tiên tải được → `sources/open/<sha256>.pdf`
   (`FULL_TEXT`, `OPEN_FULL_TEXT`, kèm URL đã chọn và giấy phép). Lời gọi tới arXiv,
   ACL Anthology và Unpaywall chờ theo `deps.source_limits` (giới hạn theo host, dùng chung
   cả tiến trình).
3. Ghi storage trước, rồi chèn `source_files` (`OPEN_ACCESS`, không chủ sở hữu, không
   reference) trong một giao dịch rào lease ngắn cho mỗi work, `ON CONFLICT DO NOTHING` trên
   index duy nhất (`work_id`, `sha256`). Mọi lời gọi mạng xong trước giao dịch đó.

Dòng đã có dùng chung cho mọi người dùng và mọi lần chạy: work đã có cả hai mức (hoặc có toàn
văn mà không có abstract) được bỏ qua. Hai lần chạy cùng ghi một work được xếp hàng bằng
advisory lock, nên mỗi work giữ nhiều nhất một dòng bản mở cho mỗi mức nội dung; tệp đã lưu
mà không được dùng là nội dung theo SHA-256, vô hại.

Lỗi của một nguồn (`ExternalServiceError`) hoặc khi tải một lựa chọn chỉ bỏ qua nguồn/lựa chọn
đó; không có bản nào thì không có dòng (S8 → `NO_ACCESSIBLE_TEXT`). Lỗi khác (DB, storage,
lỗi lập trình, mất lease) làm stage lỗi. Không log URL, DOI hay nội dung.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import AccessStatus, ContentLevel, SourceOrigin, StageName, TitleMatch
from citeproof.core.errors import ExternalServiceError, InternalError
from citeproof.db.models import SourceFile, Work
from citeproof.integrations.oa import AccessOption, AccessResolver, WorkRef, make_resolvers
from citeproof.modules.sources.fetch import (
    FetchedFile,
    FullTextFetcher,
    open_source_key,
    sha256_hex,
)
from citeproof.modules.sources.selection import run_work_ids
from citeproof.pipeline.config import load_run_info
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.pipeline.units import process_units

logger = logging.getLogger(__name__)

# Số work xử lý song song; mỗi work tải tối đa một bản `max_source_bytes` vào bộ nhớ.
WORK_CONCURRENCY = 4
_OPEN_ACCESS = SourceOrigin.OPEN_ACCESS.value


@dataclass(frozen=True)
class _WorkView:
    ref: WorkRef
    title: str
    abstract: str | None
    levels: frozenset[str]  # mức nội dung của các dòng bản mở đã có

    @property
    def needs_abstract(self) -> bool:
        has_text = bool(self.abstract and self.abstract.strip())
        return has_text and ContentLevel.ABSTRACT.value not in self.levels

    @property
    def needs_full_text(self) -> bool:
        return ContentLevel.FULL_TEXT.value not in self.levels

    @property
    def done(self) -> bool:
        return not (self.needs_abstract or self.needs_full_text)


@dataclass(frozen=True)
class _NewSource:
    access_status: AccessStatus
    content_level: ContentLevel
    storage_key: str
    sha256: str
    url: str | None = None
    license: str | None = None


class FetchSourcesStage:
    name: StageName = StageName.S5

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def run(self, ctx: StageContext) -> StageResult:
        if self._deps.metadata is None:
            raise InternalError("S5 cần gateway metadata (PipelineDeps.metadata chưa được đặt).")
        async with self._deps.session_factory() as session:
            info = await load_run_info(session, ctx.run_id)
            works = await _works(session, await run_work_ids(session, info))
        settings = self._deps.settings
        # Dựng mới mỗi lần chạy: gateway OpenAlex bên trong ngắt mạch theo lần chạy.
        resolvers = make_resolvers(
            http=self._deps.http,
            gateway_factory=self._deps.metadata,
            contact_email=settings.contact_email,
            timeout_s=settings.metadata_timeout_seconds,
            limits=self._deps.source_limits,
        )
        fetcher = FullTextFetcher(self._deps.source_http, limits=self._deps.source_limits)

        async def handle(work: _WorkView) -> None:
            sources: list[_NewSource] = []
            if work.needs_abstract:
                sources.append(await self._store_abstract(work))
            if work.needs_full_text:
                full_text = await self._store_full_text(resolvers, fetcher, work.ref)
                if full_text is not None:
                    sources.append(full_text)
            await self._write(ctx, work.ref.work_id, sources)

        report = await process_units(
            works,
            key=lambda work: str(work.ref.work_id),
            done_keys={str(work.ref.work_id) for work in works if work.done},
            handle=handle,
            concurrency=WORK_CONCURRENCY,
        )
        if report.failed:
            # Lỗi của nguồn đã được bỏ qua trong từng work: tới đây là lỗi cấp stage.
            raise report.failed[0][1]
        return StageResult()

    async def _store_abstract(self, work: _WorkView) -> _NewSource:
        data = json.dumps(
            {"title": work.title, "abstract": work.abstract}, ensure_ascii=False
        ).encode()
        digest = sha256_hex(data)
        key = open_source_key(digest, "abstract.json")
        await self._deps.storage.put(key, data)
        return _NewSource(AccessStatus.OPEN_ABSTRACT, ContentLevel.ABSTRACT, key, digest)

    async def _store_full_text(
        self, resolvers: Sequence[AccessResolver], fetcher: FullTextFetcher, ref: WorkRef
    ) -> _NewSource | None:
        found = await first_full_text(resolvers, fetcher, ref)
        if found is None:
            return None
        option, fetched = found
        key = open_source_key(fetched.sha256, "pdf")
        await self._deps.storage.put(key, fetched.data)
        return _NewSource(
            AccessStatus.OPEN_FULL_TEXT,
            ContentLevel.FULL_TEXT,
            key,
            fetched.sha256,
            url=option.url,
            license=option.license,
        )

    async def _write(self, ctx: StageContext, work_id: UUID, sources: list[_NewSource]) -> None:
        if not sources:
            return
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            lock_key = func.hashtextextended(f"source_files:{work_id}", 0)
            await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            existing = set(
                (
                    await session.execute(
                        select(SourceFile.content_level).where(
                            SourceFile.work_id == work_id, SourceFile.origin == _OPEN_ACCESS
                        )
                    )
                ).scalars()
            )
            for source in sources:
                if source.content_level.value in existing:
                    continue  # lần chạy khác vừa ghi mức này cho work
                await session.execute(
                    insert(SourceFile)
                    .values(
                        work_id=work_id,
                        reference_entry_id=None,
                        owner_user_id=None,
                        origin=_OPEN_ACCESS,
                        access_status=source.access_status.value,
                        content_level=source.content_level.value,
                        url=source.url,
                        license=source.license,
                        storage_key=source.storage_key,
                        sha256=source.sha256,
                        title_match=TitleMatch.UNKNOWN.value,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["work_id", "sha256"],
                        index_where=text(f"origin = '{_OPEN_ACCESS}'"),
                    )
                )


async def first_full_text(
    resolvers: Sequence[AccessResolver], fetcher: FullTextFetcher, ref: WorkRef
) -> tuple[AccessOption, FetchedFile] | None:
    """Bản PDF đầu tiên tải được theo thứ tự nguồn; nguồn lỗi chỉ bị bỏ qua.

    Nguồn sau chỉ được hỏi khi mọi lựa chọn của nguồn trước đều không tải được; một URL do
    hai nguồn cùng báo chỉ được tải một lần.
    """
    tried: set[str] = set()
    for resolver in resolvers:
        try:
            options = await resolver.resolve(ref)
        except ExternalServiceError as exc:
            logger.warning(
                "open_access_resolve_failed resolver=%s code=%s",
                type(resolver).__name__,
                exc.code,
                extra={"error_type": type(exc).__name__},
            )
            continue
        for option in options:
            if option.url in tried:
                continue
            tried.add(option.url)
            fetched = await fetcher.fetch(option)
            if fetched is not None:
                return option, fetched
    return None


async def _works(session: AsyncSession, work_ids: list[UUID]) -> list[_WorkView]:
    if not work_ids:
        return []
    rows = {
        row.id: row
        for row in (
            await session.execute(
                select(Work.id, Work.doi, Work.arxiv_id, Work.title, Work.abstract).where(
                    Work.id.in_(work_ids)
                )
            )
        ).all()
    }
    levels: dict[UUID, set[str]] = defaultdict(set)
    existing = await session.execute(
        select(SourceFile.work_id, SourceFile.content_level).where(
            SourceFile.origin == _OPEN_ACCESS, SourceFile.work_id.in_(work_ids)
        )
    )
    for work_id, level in existing.all():
        if work_id is not None:
            levels[work_id].add(level)
    return [
        _WorkView(
            ref=WorkRef(work_id=work_id, doi=row.doi, arxiv_id=row.arxiv_id),
            title=row.title,
            abstract=row.abstract,
            levels=frozenset(levels[work_id]),
        )
        for work_id in work_ids
        if (row := rows.get(work_id)) is not None
    ]
