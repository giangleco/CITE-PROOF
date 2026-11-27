"""S8 — phán quyết cho từng cặp claim-reference (spec §7.8, §6.4; T-502, T-504, QĐ-32).

Đơn vị là cặp claim-reference của `load_pairs` (kể cả link NULL); cặp đã có dòng
`claim_verifications` cho verifier của lần chạy được bỏ qua. Mỗi cặp: tiền kiểm tất định,
giới hạn số cặp (`limits.max_claims`, theo thứ tự trong bài), rồi verifier của biến thể (đọc DB
và gọi W5 ngoài giao dịch), cuối cùng một `fenced_session` ngắn để ghi. Hạn mức chi phí
(`limits.max_cost_usd`) được verifier kiểm ngay trước mỗi lời gọi LLM (`llm_budget`): lời gọi
đầu của cặp giữ chỗ bằng chi phí đầu ra tối đa của W5 (`judge_reserve`), trả lại khi cặp xong
phần LLM, nên các cặp chạy song song không cùng vượt hạn mức; cặp có kết quả tất định (kể cả V2
không có chunk truy xuất) giữ kết quả đó.

Cô lập theo cặp: `CiteProofError` của một cặp cho dòng `PROCESSING_ERROR` của cặp đó (log
chỉ tên lớp lỗi), cặp khác vẫn chạy. Ngoại lệ: `InternalError`, `LeaseLostError`, lỗi cache
phát lại luôn làm stage lỗi; `RetryableError` làm stage thử lại (trừ lần thử cuối). Lỗi khác
(DB, lỗi lập trình) làm stage lỗi.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import LLMTask, SourceOrigin, StageName, UnverifiableReason
from citeproof.core.errors import CiteProofError, InternalError, LeaseLostError, RetryableError
from citeproof.llm.types import CacheEntryInvalidError, CacheMissError, CallContext
from citeproof.modules.references.stage_extract import LLM_NOT_CONFIGURED
from citeproof.modules.retrieval.pairs import ClaimPair, SourceChoice, choose_sources, load_pairs
from citeproof.modules.verification.context import RefMeta
from citeproof.modules.verification.precheck import PairState, precheck
from citeproof.modules.verification.store import (
    done_keys,
    pair_key,
    reference_meta,
    reference_statuses,
    run_cost,
    write_outcomes,
)
from citeproof.modules.verification.verifiers import make_verifier
from citeproof.modules.verification.verifiers.base import (
    PairVerifier,
    VerificationContext,
    VerificationOutcome,
    deterministic,
)
from citeproof.pipeline.config import RunConfig, RunInfo, load_run_info
from citeproof.pipeline.deps import PipelineDeps, call_context
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult
from citeproof.pipeline.units import process_units

__all__ = ["CLAIM_LIMIT_REACHED", "COST_LIMIT_REACHED", "VerifyStage", "judge_reserve"]

CLAIM_LIMIT_REACHED = "CLAIM_LIMIT_REACHED"
COST_LIMIT_REACHED = "COST_LIMIT_REACHED"

logger = logging.getLogger(__name__)

_NO_REF = RefMeta(title=None, authors=(), year=None, venue=None)


@dataclass(frozen=True)
class _Unit:
    state: PairState
    ref: RefMeta

    @property
    def pair(self) -> ClaimPair:
        return self.state.pair


def judge_reserve(config: RunConfig, prices: Mapping[str, tuple[float, float]]) -> Decimal:
    """Chi phí đầu ra tối đa của một lời gọi W5: giá đầu ra * `max_tokens` (model lạ → 0)."""
    key = LLMTask.VERDICT_JUDGE.value
    model = config.models[key]
    if model not in prices:
        return Decimal(0)
    return Decimal(str(prices[model][1])) * config.max_tokens[key] / Decimal(1_000_000)


class _Gate(Protocol):
    async def allow(self, reserve: Decimal) -> bool: ...

    async def release(self, reserve: Decimal) -> None: ...


class _CostGate:
    """Hạn mức chi phí có giữ chỗ cho cặp đang gọi LLM (QĐ-32).

    `allow(reserve)` được phép khi `run_cost + reserved + reserve < limit`; không đủ chỗ chỉ vì
    chỗ đang giữ thì chờ `release` rồi xét lại. `run_cost + reserve >= limit` → `reached`, từ đó
    mọi lời gọi đều bị từ chối. `reserve = 0` (lời gọi sau của cặp đã giữ chỗ, hoặc model không
    có giá) bỏ qua chỗ đang giữ và không bao giờ chờ, nên cặp không tự chặn chính nó.
    """

    def __init__(self, read_cost: Callable[[], Awaitable[Decimal]], limit: Decimal) -> None:
        self._read_cost = read_cost
        self._limit = limit
        self._cond = asyncio.Condition()
        self.reserved = Decimal(0)
        self.reached = False

    async def allow(self, reserve: Decimal) -> bool:
        async with self._cond:
            while not self.reached:
                cost = await self._read_cost()
                if cost + reserve >= self._limit:
                    self.reached = True
                    self._cond.notify_all()  # cặp đang chờ chỗ cũng dừng ngay
                elif reserve == 0 or cost + self.reserved + reserve < self._limit:
                    self.reserved += reserve
                    return True
                else:  # chỉ thiếu vì chỗ đang giữ (`reserved > 0`): chờ một `release`
                    await self._cond.wait()
            return False

    async def release(self, reserve: Decimal) -> None:
        async with self._cond:
            self.reserved -= reserve
            self._cond.notify_all()


class _PairBudget:
    """`llm_budget` của một cặp: lời gọi đầu giữ chỗ `reserve`, các lời gọi sau kiểm với 0;
    `release` trả chỗ đúng một lần (stage gọi trong `finally` sau khi cặp xong phần LLM)."""

    def __init__(self, gate: _Gate, reserve: Decimal) -> None:
        self._gate = gate
        self._reserve = reserve
        self._held = False

    async def __call__(self) -> bool:
        if self._held:
            return await self._gate.allow(Decimal(0))
        self._held = await self._gate.allow(self._reserve)
        return self._held

    async def release(self) -> None:
        if self._held:
            self._held = False
            await self._gate.release(self._reserve)


class VerifyStage:
    name: StageName = StageName.S8

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def run(self, ctx: StageContext) -> StageResult:
        llm_configured = self._deps.llm is not None
        async with self._deps.session_factory() as session:
            info = await load_run_info(session, ctx.run_id)
            units = await self._units(session, info)
            done = await done_keys(session, info.run_id, info.config.variant.value)

        variant = info.config.variant
        max_claims = info.config.limits.max_claims
        warnings: list[str] = []
        if any(unit.pair.position >= max_claims for unit in units):
            warnings.append(CLAIM_LIMIT_REACHED)
        if not llm_configured and any(
            unit.pair.position < max_claims
            and precheck(unit.state, variant=variant, llm_configured=True) is None
            for unit in units
        ):
            warnings.append(LLM_NOT_CONFIGURED)

        async def read_cost() -> Decimal:
            async with self._deps.session_factory() as session:
                return await run_cost(session, info.run_id)

        gate = _CostGate(read_cost, Decimal(str(info.config.limits.max_cost_usd)))
        reserve = judge_reserve(info.config, self._deps.settings.llm_prices)
        verifier = make_verifier(variant, self._deps) if llm_configured else None
        call_ctx = call_context(self._deps, info, StageName.S8)

        async def handle(unit: _Unit) -> None:
            budget = _PairBudget(gate, reserve)
            try:
                outcomes = await self._outcomes(ctx, info, unit, budget, verifier, call_ctx)
            finally:
                await budget.release()
            async with fenced_session(self._deps.session_factory, ctx.lease) as session:
                await write_outcomes(session, info=info, pair=unit.pair, outcomes=outcomes)

        report = await process_units(
            units,
            key=lambda unit: pair_key(unit.pair.claim_id, unit.pair.reference_entry_id),
            done_keys=done,
            handle=handle,
            concurrency=max(1, self._deps.settings.llm_concurrency),
        )
        if report.failed:
            raise report.failed[0][1]
        if gate.reached:
            warnings.append(COST_LIMIT_REACHED)
        return StageResult(warnings=tuple(warnings))

    async def _units(self, session: AsyncSession, info: RunInfo) -> list[_Unit]:
        pairs = await load_pairs(session, info)
        statuses = await reference_statuses(session, info)
        metas = await reference_meta(session, info)
        choices: dict[UUID, SourceChoice] = {}
        units: list[_Unit] = []
        for pair in pairs:
            ref_id = pair.reference_entry_id
            sources = SourceChoice(None, None)
            if ref_id is not None:
                if ref_id not in choices:
                    choices[ref_id] = await choose_sources(session, info, ref_id)
                sources = choices[ref_id]
            full = sources.full_text
            state = PairState(
                pair=pair,
                reference_status=statuses.get(ref_id) if ref_id is not None else None,
                has_upload=full is not None and full.origin is SourceOrigin.USER_UPLOAD,
                sources=sources,
            )
            units.append(_Unit(state, metas.get(ref_id, _NO_REF) if ref_id else _NO_REF))
        return units

    async def _outcomes(
        self,
        ctx: StageContext,
        info: RunInfo,
        unit: _Unit,
        budget: _PairBudget,
        verifier: PairVerifier | None,
        call_ctx: CallContext,
    ) -> list[VerificationOutcome]:
        variant = info.config.variant
        error = UnverifiableReason.PROCESSING_ERROR
        if unit.pair.position >= info.config.limits.max_claims:
            return [deterministic(info, error, variant)]
        reason = precheck(unit.state, variant=variant, llm_configured=verifier is not None)
        if reason is not None:
            return [deterministic(info, reason, variant)]
        assert verifier is not None  # precheck đã trả PROCESSING_ERROR khi không có LLM
        vc = VerificationContext(
            info=info,
            pair=unit.pair,
            sources=unit.state.sources,
            ref=unit.ref,
            call_ctx=call_ctx,
            llm_budget=budget,
            lease=ctx.lease,
            is_last_attempt=ctx.is_last_attempt,
        )
        try:
            return await verifier.verify(vc)
        except (InternalError, LeaseLostError, CacheMissError, CacheEntryInvalidError):
            raise
        except RetryableError as exc:
            if not ctx.is_last_attempt:
                raise
            logger.warning("s8_pair_failed", extra={"error_type": type(exc).__name__})
        except CiteProofError as exc:
            logger.warning("s8_pair_failed", extra={"error_type": type(exc).__name__})
        return [deterministic(info, error, variant)]
