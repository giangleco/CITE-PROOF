"""S9 — hoàn tất lần chạy (spec §7.9; T-601, QĐ-32).

Ghi `verification_runs.cost_estimate` = tổng `llm_calls.cost_estimate` của lần chạy trong một
`fenced_session`. Báo cáo không được dựng sẵn: `services.report` đọc thẳng các bảng kết quả
khi người dùng xem. Lần chạy con (chép kết quả ngoài phạm vi từ lần chạy cha, QĐ-30) do
Task 20 thêm vào stage này.
"""

from sqlalchemy import update

from citeproof.core.enums import StageName
from citeproof.db.models import VerificationRun
from citeproof.modules.verification.store import run_cost
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.jobs import fenced_session
from citeproof.pipeline.stages import StageContext, StageResult

__all__ = ["FinalizeStage"]


class FinalizeStage:
    name: StageName = StageName.S9

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def run(self, ctx: StageContext) -> StageResult:
        async with fenced_session(self._deps.session_factory, ctx.lease) as session:
            cost = await run_cost(session, ctx.run_id)
            await session.execute(
                update(VerificationRun)
                .where(VerificationRun.id == ctx.run_id)
                .values(cost_estimate=cost)
            )
        return StageResult()
