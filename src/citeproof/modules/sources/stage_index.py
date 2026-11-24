"""S6 — chia đoạn và lập chỉ mục tệp nguồn (spec §7.6, §6.4; T-405, ADR-007).

Đơn vị là một tệp của `selection.relevant_source_files`; tệp đã có `source_chunks` được bỏ
qua, nên chunk của bản mở tạo một lần và dùng chung cho mọi lần chạy, mọi người dùng. Mỗi tệp,
song song tối đa `FILE_CONCURRENCY`:

1. `parse_source` (GROBID gọi ngoài mọi giao dịch; `pypdfium2` dự phòng ở lần thử cuối hoặc
   khi không cấu hình GROBID), rồi ghi `<storage_key>.parsed.json`.
2. `chunk_document` trong luồng riêng; `deps.embedder.embed` trên văn bản các chunk
   (`NoEmbedder` → `embedding`, `embedding_model` NULL). `tsv` là cột sinh sẵn trong DB.
3. Chèn mọi chunk của tệp trong một giao dịch rào lease: khóa dòng `source_files`, kiểm tra lại
   chưa có chunk (lần chạy khác có thể vừa ghi xong), rồi chèn. Thử lại sau lỗi giữa chừng hay
   hai lần chạy cùng lúc đều không ghi trùng.

Tệp không cho ra chữ nào thì không có chunk (S8 → `SOURCE_PARSE_FAILED`). Lỗi khác (GROBID không
truy cập được khi còn lượt thử, DB, storage, lỗi lập trình, mất lease) làm stage lỗi. Không log
nội dung nguồn.
"""

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import StageName
from citeproof.core.errors import InternalError
from citeproof.db.models import SourceChunk, SourceFile
from citeproof.db.models.sources import EMBEDDING_DIM
from citeproof.modules.sources.chunking import ChunkDraft, chunk_document
from citeproof.modules.sources.parse_source import parse_source, parsed_source_key
from citeproof.modules.sources.selection import relevant_source_files
from citeproof.pipeline.config import load_run_info
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.pipeline.units import process_units

# Số tệp xử lý song song: mỗi tệp giữ một PDF (≤ `max_source_bytes`) trong bộ nhớ và có thể
# chiếm một luồng xử lý của GROBID.
FILE_CONCURRENCY = 2


class IndexSourcesStage:
    name: StageName = StageName.S6

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def run(self, ctx: StageContext) -> StageResult:
        async with self._deps.session_factory() as session:
            info = await load_run_info(session, ctx.run_id)
            files = await relevant_source_files(session, info)
            indexed = await _indexed(session, [f.id for f in files])
        allow_fallback = ctx.is_last_attempt or self._deps.grobid is None

        async def handle(source: SourceFile) -> None:
            await self._index(ctx, source, allow_fallback=allow_fallback)

        report = await process_units(
            files,
            key=lambda source: str(source.id),
            done_keys={str(source_id) for source_id in indexed},
            handle=handle,
            concurrency=FILE_CONCURRENCY,
        )
        if report.failed:
            # Tệp không đọc được đã thành "không có chunk": tới đây là lỗi cấp stage.
            raise report.failed[0][1]
        return StageResult()

    async def _index(self, ctx: StageContext, source: SourceFile, *, allow_fallback: bool) -> None:
        parsed = await parse_source(self._deps, source, allow_fallback=allow_fallback)
        if parsed is None:
            return
        # Ghi storage trước giao dịch rào (giữ giao dịch ngắn); thử lại thì ghi đè.
        await self._deps.storage.put(
            parsed_source_key(source.storage_key), parsed.model_dump_json().encode()
        )
        chunks = await asyncio.to_thread(chunk_document, parsed)
        if not chunks:
            return
        vectors = await self._embed(chunks)
        await self._write(ctx, source.id, chunks, vectors)

    async def _embed(self, chunks: Sequence[ChunkDraft]) -> list[list[float]] | None:
        vectors = await self._deps.embedder.embed([chunk.text for chunk in chunks])
        if vectors is None:
            return None
        if len(vectors) != len(chunks) or any(len(vector) != EMBEDDING_DIM for vector in vectors):
            raise InternalError("Embedder trả số vector hoặc số chiều không khớp với các chunk.")
        return vectors

    async def _write(
        self,
        ctx: StageContext,
        source_id: UUID,
        chunks: Sequence[ChunkDraft],
        vectors: list[list[float]] | None,
    ) -> None:
        model = self._deps.embedder.model if vectors is not None else None
        rows: list[dict[str, Any]] = [
            {
                "source_file_id": source_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "page": chunk.page,
                "section": chunk.section,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "region": chunk.region.value,
                "language": chunk.language,
                "embedding": vectors[index] if vectors is not None else None,
                "embedding_model": model,
            }
            for index, chunk in enumerate(chunks)
        ]
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            # FOR NO KEY UPDATE: hai lần chạy ghi chunk cho cùng tệp xếp hàng tại đây.
            locked = await session.scalar(
                select(SourceFile.id)
                .where(SourceFile.id == source_id)
                .with_for_update(key_share=True)
            )
            if locked is None:
                return  # tệp vừa bị xóa (người dùng xóa tệp tải lên)
            if await _has_chunks(session, source_id):
                return  # lần chạy khác vừa lập chỉ mục tệp này
            await session.execute(insert(SourceChunk), rows)


async def _has_chunks(session: AsyncSession, source_id: UUID) -> bool:
    found = await session.scalar(
        select(SourceChunk.id).where(SourceChunk.source_file_id == source_id).limit(1)
    )
    return found is not None


async def _indexed(session: AsyncSession, source_ids: list[UUID]) -> set[UUID]:
    if not source_ids:
        return set()
    rows = await session.execute(
        select(SourceChunk.source_file_id)
        .where(SourceChunk.source_file_id.in_(source_ids))
        .distinct()
    )
    return set(rows.scalars())
