"""Tiến độ một lần chạy, chi tiết kỹ thuật cho admin, tiếp tục sau lỗi (spec web UI §7.4).

Trạng thái từng bước suy ra từ bảng `jobs`; bước chưa có job là "pending".
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core import errors
from citeproof.core.enums import JobStatus, RunStatus, StageName
from citeproof.core.errors import NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, Job, LLMCall, User, VerificationRun
from citeproof.pipeline.config import RunConfig, ensure_same_provider
from citeproof.pipeline.runs import resume_run
from citeproof.services.documents import is_sample

StepState = Literal["pending", "queued", "running", "done", "failed"]
GENERIC_STAGE_ERROR = "Lỗi hệ thống ở bước này."
_STATE: dict[str, StepState] = {
    JobStatus.QUEUED.value: "queued",
    JobStatus.RUNNING.value: "running",
    JobStatus.DONE.value: "done",
    JobStatus.FAILED.value: "failed",
}
_ACTIVE = {RunStatus.QUEUED, RunStatus.RUNNING}


def _domain_error_names() -> frozenset[str]:
    names: set[str] = set()
    stack: list[type[errors.CiteProofError]] = [errors.CiteProofError]
    while stack:
        cls = stack.pop()
        names.add(cls.__name__)
        stack.extend(cls.__subclasses__())
    names.discard(errors.InternalError.__name__)
    return frozenset(names)


@dataclass(frozen=True)
class StepView:
    stage: StageName
    state: StepState
    attempts: int
    queued_at: datetime | None
    # Lần nhận job gần nhất (T-209); trang admin hiện thời lượng khi có cả hai.
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class RunView:
    id: UUID
    document_id: UUID
    document_name: str
    is_sample: bool
    status: RunStatus
    steps: list[StepView]
    references_ready: bool
    failed_stage: StageName | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    config: dict[str, Any]
    is_active: bool


@dataclass(frozen=True)
class TechDetails:
    config: dict[str, Any]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost: Decimal
    models: list[str]
    prompt_versions: list[str]
    other_runs: list[tuple[UUID, RunStatus, datetime]]


async def own_run(session: AsyncSession, *, user: User, run_id: UUID) -> VerificationRun:
    run = (
        await session.execute(
            select(VerificationRun).where(
                VerificationRun.id == run_id, VerificationRun.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError()
    return run


async def get_run_view(session: AsyncSession, *, user: User, run_id: UUID) -> RunView:
    run = await own_run(session, user=user, run_id=run_id)
    document = await session.get(Document, run.document_id)
    if document is None:
        raise NotFoundError()
    jobs = {
        job.stage: job
        for job in (await session.execute(select(Job).where(Job.run_id == run.id))).scalars()
    }
    steps = []
    for stage in StageName:
        job = jobs.get(stage.value)
        steps.append(
            StepView(
                stage=stage,
                state=_STATE[job.status] if job else "pending",
                attempts=job.attempts if job else 0,
                queued_at=job.created_at if job else None,
                started_at=job.started_at if job else None,
                finished_at=job.finished_at if job else None,
                last_error=job.last_error if job else None,
            )
        )
    status = RunStatus(run.status)
    error = run.error or {}
    failed_stage: StageName | None = None
    message: str | None = None
    if status == RunStatus.FAILED:
        stage_value = error.get("stage")
        failed_stage = StageName(stage_value) if stage_value in set(StageName) else None
        error_type = error.get("type")
        if error_type in _domain_error_names() and error.get("message"):
            # describe_error lưu dạng "<Loại>: <thông điệp>"; người dùng chỉ cần thông điệp.
            message = str(error["message"]).removeprefix(f"{error_type}: ")
        else:
            message = GENERIC_STAGE_ERROR
    s3 = jobs.get(StageName.S3.value)
    return RunView(
        id=run.id,
        document_id=document.id,
        document_name=document.filename,
        is_sample=is_sample(document),
        status=status,
        steps=steps,
        references_ready=s3 is not None and s3.status == JobStatus.DONE.value,
        failed_stage=failed_stage,
        error_message=message,
        created_at=run.created_at,
        finished_at=run.finished_at,
        config=dict(run.config or {}),
        is_active=status in _ACTIVE,
    )


async def get_tech_details(session: AsyncSession, *, user: User, run_id: UUID) -> TechDetails:
    run = await own_run(session, user=user, run_id=run_id)
    totals = (
        await session.execute(
            select(
                func.count(LLMCall.id),
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
                func.coalesce(func.sum(LLMCall.cost_estimate), 0),
            ).where(LLMCall.run_id == run.id)
        )
    ).one()
    models = (
        await session.execute(
            select(LLMCall.model, LLMCall.prompt_version).where(LLMCall.run_id == run.id).distinct()
        )
    ).all()
    others = (
        await session.execute(
            select(VerificationRun.id, VerificationRun.status, VerificationRun.created_at)
            .where(VerificationRun.document_id == run.document_id, VerificationRun.id != run.id)
            .order_by(VerificationRun.created_at.desc())
        )
    ).all()
    return TechDetails(
        config=dict(run.config or {}),
        llm_calls=int(totals[0]),
        input_tokens=int(totals[1]),
        output_tokens=int(totals[2]),
        cost=Decimal(totals[3]),
        models=sorted({m for m, _ in models}),
        prompt_versions=sorted({p for _, p in models}),
        other_runs=[(rid, RunStatus(st), created) for rid, st, created in others],
    )


async def resume(session: AsyncSession, settings: Settings, *, user: User, run_id: UUID) -> None:
    run = await own_run(session, user=user, run_id=run_id)
    if RunStatus(run.status) in _ACTIVE:
        return  # bấm "Tiếp tục" hai lần: lần chạy đã chạy lại rồi
    # Provider đã đổi: chạy tiếp chỉ làm stage cần LLM lỗi lại, nên từ chối ngay (spec §5.4).
    ensure_same_provider(RunConfig.load(run.config), settings)
    demo = (run.config or {}).get("demo")
    if settings.env == "local" and isinstance(demo, dict) and demo.get("fail_at"):
        config = {**run.config, "demo": {**demo, "fail_at": None}}
        await session.execute(
            update(VerificationRun).where(VerificationRun.id == run.id).values(config=config)
        )
    await resume_run(session, run.id)
