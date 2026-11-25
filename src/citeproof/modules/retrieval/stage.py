"""S7 — truy xuất bằng chứng trong nguồn đã chọn (spec §7.7, §6.4; T-501, QĐ-24).

Đơn vị là cặp claim-nguồn toàn văn có chunk; cặp đã có `retrieval_results` được bỏ qua nên
tiếp tục sau lỗi không gọi lại W3 hay ghi trùng. V0 không truy xuất; V3 truy xuất theo
từng ý ngay trong S8; V1 không truy xuất gì (S8 đọc mọi chunk abstract). Mỗi cặp: W3 và truy
vấn DB trước, rồi một giao dịch rào lease ngắn để ghi. Mọi lỗi ngoài lỗi LLM của W3 (đã được
`Retriever` xử lý) làm stage lỗi.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import StageName, Verifier
from citeproof.db.models import RetrievalResult
from citeproof.modules.retrieval.pairs import ClaimPair, SourceChoice, choose_sources, load_pairs
from citeproof.modules.retrieval.retriever import Retriever
from citeproof.pipeline.config import RunInfo, load_run_info
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.pipeline.units import process_units

__all__ = ["RetrieveStage"]

_SKIPPED_VARIANTS = frozenset({Verifier.V0, Verifier.V1, Verifier.V3})


@dataclass(frozen=True)
class _Unit:
    pair: ClaimPair
    source_file_id: UUID


class RetrieveStage:
    name: StageName = StageName.S7

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps
        self._retriever = Retriever(deps)

    async def run(self, ctx: StageContext) -> StageResult:
        async with self._deps.session_factory() as session:
            info = await load_run_info(session, ctx.run_id)
            if info.config.variant in _SKIPPED_VARIANTS:
                return StageResult()
            units = await self._units(session, info)
            done = await _done(session, info.run_id)

        async def handle(unit: _Unit) -> None:
            await self._handle(ctx, info, unit)

        report = await process_units(
            units,
            key=_key,
            done_keys=done,
            handle=handle,
            concurrency=max(1, self._deps.settings.llm_concurrency),
        )
        if report.failed:
            raise report.failed[0][1]
        return StageResult()

    async def _units(self, session: AsyncSession, info: RunInfo) -> list[_Unit]:
        choices: dict[UUID | None, SourceChoice] = {}
        units: dict[str, _Unit] = {}
        for pair in await load_pairs(session, info):
            if pair.reference_entry_id is None or not pair.is_claim:
                continue
            if pair.reference_entry_id not in choices:
                choices[pair.reference_entry_id] = await choose_sources(
                    session, info, pair.reference_entry_id
                )
            full = choices[pair.reference_entry_id].full_text
            if full is None or full.chunk_count == 0:
                continue
            unit = _Unit(pair, full.source_file_id)
            units.setdefault(_key(unit), unit)
        return list(units.values())

    async def _handle(self, ctx: StageContext, info: RunInfo, unit: _Unit) -> None:
        # Mạng/LLM và truy vấn đọc trước; không giữ giao dịch ghi trong lúc chờ.
        async with self._deps.session_factory() as session:
            output = await self._retriever.retrieve(
                session,
                info=info,
                pair=unit.pair,
                source_file_id=unit.source_file_id,
                ctx=self._retriever.call_context(info),
                last_attempt=ctx.is_last_attempt,
            )
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            await self._retriever.write_results(
                session,
                info=info,
                pair=unit.pair,
                source_file_id=unit.source_file_id,
                output=output,
            )


def _key(unit: _Unit) -> str:
    return f"{unit.pair.claim_id}:{unit.source_file_id}"


async def _done(session: AsyncSession, run_id: UUID) -> set[str]:
    rows = await session.execute(
        select(RetrievalResult.claim_id, RetrievalResult.source_file_id)
        .where(RetrievalResult.run_id == run_id)
        .distinct()
    )
    return {f"{claim_id}:{source_id}" for claim_id, source_id in rows}
