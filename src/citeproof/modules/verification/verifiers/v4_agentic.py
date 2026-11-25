"""V4 — A2 Evidence Investigator cho mọi cặp có toàn văn (spec §9, `07a` §4.5.3; T-704).

A2 tự điều hướng trong toàn văn của đúng cặp (`source_outline`, `source_search`, `source_read`,
`submit_verdict`); phạm vi khóa bằng `SourceSession` dựng từ `source_file_id` của cặp. Bản nộp
qua `apply_rules` như W5, với bảng chunk chỉ gồm chunk agent đã đọc, nên chunk chưa đọc không bao
giờ thành bằng chứng kể cả khi runner nhận bản nộp cuối sau khi hết lượt sửa.

Không có toàn văn (chỉ abstract) → như V1, W5 trên abstract (preflight F30); không có nguồn đã
được tiền kiểm xử lý. Hạn mức chi phí kiểm trước khi bắt đầu agent. Agent không nộp (hết ngân
sách, không nộp, lỗi) → `PROCESSING_ERROR` gắn cờ. `RetryableError` ném lại để job thử lại, trừ
lần thử cuối thì coi như lỗi agent (giữ `agent_run_id` của dòng `agent_runs` ERROR). Quote được
giải escape của data block trước khi kiểm tra nguyên văn, như W5. Không log prompt, văn bản nguồn
hay đầu ra LLM.
"""

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import select

from citeproof.core.enums import (
    AgentName,
    AgentRunStatus,
    AgentTargetType,
    ClaimStatus,
    ContentLevelUsed,
    LLMTask,
    UnverifiableReason,
    Verifier,
)
from citeproof.core.errors import InternalError, RetryableError
from citeproof.db.models.agents import AgentRun
from citeproof.llm.agent_runner import AgentBudget
from citeproof.llm.context import render_data_block
from citeproof.llm.tools import SubmitTool, load_tool_catalog
from citeproof.modules.retrieval.pairs import SourceRef
from citeproof.modules.retrieval.tools import (
    make_source_tools,
    open_source_session,
    verdict_check,
)
from citeproof.modules.verification.context import language_name, reference_block
from citeproof.modules.verification.explanations import explanation_language
from citeproof.modules.verification.quotes import unescape_evidence
from citeproof.modules.verification.rules import RuleContext, apply_rules
from citeproof.modules.verification.verdict import Verdict
from citeproof.modules.verification.verifiers.base import (
    SCHEMA_VERSION,
    LLMVerifier,
    VerificationContext,
    VerificationOutcome,
    deterministic,
    guarded,
    title_mismatch,
)
from citeproof.pipeline.config import AgentConfig
from citeproof.pipeline.deps import PROMPTS_ROOT, PipelineDeps

__all__ = [
    "A2Result",
    "AgenticVerifier",
    "FirstPass",
    "agent_config",
    "flagged_error",
    "investigate",
]

TOOL_CATALOG = "source"
SUBMIT_NAME = "submit_verdict"
PROMPT_TASK = LLMTask.AGENT_EVIDENCE_INVESTIGATOR
NO_FIRST_PASS = "(none)"


@dataclass(frozen=True)
class FirstPass:
    """Kết quả W5 cho A2 ở chế độ `V2+A2`: trạng thái và chunk bằng chứng, không giải thích."""

    status: ClaimStatus
    evidence_chunk_ids: Sequence[UUID]


@dataclass(frozen=True)
class A2Result:
    """`outcome` None: agent không cho phán quyết dùng được (không nộp, hết ngân sách, lỗi)."""

    outcome: VerificationOutcome | None
    agent_run_id: UUID | None


@cache
def _catalog(version: str) -> Mapping[str, str]:
    return MappingProxyType(load_tool_catalog(PROMPTS_ROOT, TOOL_CATALOG, version))


def agent_config(vc: VerificationContext) -> AgentConfig:
    cfg = vc.info.config.agents.get(AgentName.EVIDENCE_INVESTIGATOR.value)
    if cfg is None:  # `RunConfig` luôn gộp mặc định cho mọi agent
        raise InternalError("RunConfig thiếu cấu hình evidence_investigator")
    return cfg


def flagged_error(
    vc: VerificationContext, verifier: Verifier, agent_run_id: UUID | None
) -> VerificationOutcome:
    """`PROCESSING_ERROR` gắn cờ (agent không cho kết quả), giữ `agent_run_id` nếu có."""
    base = deterministic(vc.info, UnverifiableReason.PROCESSING_ERROR, verifier)
    outcome = dataclasses.replace(base.outcome, needs_user_review=True)
    return dataclasses.replace(base, outcome=outcome, agent_run_id=agent_run_id)


async def investigate(
    deps: PipelineDeps,
    vc: VerificationContext,
    source: SourceRef,
    *,
    verifier: Verifier,
    first_pass: FirstPass | None,
) -> A2Result:
    """Chạy A2 trên toàn văn `source` (gọi sau khi đã qua hạn mức chi phí) rồi `apply_rules`."""
    runner = deps.agents
    if runner is None:
        raise InternalError("S8 gọi A2 khi chưa cấu hình AgentRunner")
    info = vc.info
    ss = await open_source_session(deps.session_factory, source.source_file_id)
    catalog_version = info.config.tool_catalogs[TOOL_CATALOG]
    descriptions = _catalog(catalog_version)
    tools = make_source_tools(deps.session_factory, ss, descriptions)
    submit = SubmitTool(
        name=SUBMIT_NAME,
        description=descriptions[SUBMIT_NAME],
        model=Verdict,
        check=verdict_check(ss),
    )
    language = explanation_language(info.document_language)
    spec = info.config.task(PROMPT_TASK)
    parts = deps.prompts.render(
        PROMPT_TASK.value,
        spec.prompt_version,
        reference=reference_block(vc.ref, ContentLevelUsed.FULL_TEXT),
        claim=render_data_block("claim", vc.pair.claim_text, attrs={"lang": vc.pair.language}),
        context=render_data_block("context", vc.pair.context_text),
        first_pass=_first_pass_block(first_pass, ss.short_id),
        language=language_name(language),
    )
    try:
        outcome = await runner.run(
            agent=AgentName.EVIDENCE_INVESTIGATOR,
            version=spec.prompt_version,
            tool_catalog_version=catalog_version,
            parts=parts,
            tools=tools,
            submit=submit,
            budget=AgentBudget.from_config(agent_config(vc)),
            spec=spec,
            ctx=vc.call_ctx,
            target_type=AgentTargetType.CLAIM,
            target_id=vc.pair.claim_id,
        )
    except RetryableError:
        if not vc.is_last_attempt:
            raise
        # Lần thử cuối: runner đã ghi dòng agent_runs ERROR; giữ id của nó trên dòng kết quả.
        return A2Result(None, await _latest_agent_run(deps, vc))
    if outcome.status is not AgentRunStatus.SUBMITTED or outcome.output is None:
        return A2Result(None, outcome.agent_run_id)
    read = {short: ss.chunks[short] for short in ss.read}
    ruled = apply_rules(
        unescape_evidence(outcome.output),
        read,
        RuleContext(
            content_level=ContentLevelUsed.FULL_TEXT,
            verifier=verifier,
            title_mismatch=title_mismatch(source),
            language=language,
        ),
    )
    return A2Result(
        VerificationOutcome(
            outcome=guarded(ruled, info),
            model=outcome.model,
            prompt_version=spec.prompt_version,
            schema_version=SCHEMA_VERSION,
            verifier=verifier,
            agent_run_id=outcome.agent_run_id,
        ),
        outcome.agent_run_id,
    )


async def _latest_agent_run(deps: PipelineDeps, vc: VerificationContext) -> UUID | None:
    """Dòng `agent_runs` mới nhất của A2 cho claim này trong lần chạy (phiên đọc ngắn)."""
    async with deps.session_factory() as session:
        found: UUID | None = await session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.run_id == vc.info.run_id,
                AgentRun.agent == AgentName.EVIDENCE_INVESTIGATOR.value,
                AgentRun.target_type == AgentTargetType.CLAIM.value,
                AgentRun.target_id == vc.pair.claim_id,
            )
            .order_by(AgentRun.started_at.desc().nulls_last(), AgentRun.id.desc())
            .limit(1)
        )
    return found


def _first_pass_block(first_pass: FirstPass | None, short_id: Callable[[UUID], str | None]) -> str:
    """Trạng thái W5 và chunk bằng chứng (theo ID của phiên A2); không có lời giải thích."""
    if first_pass is None:
        return NO_FIRST_PASS
    ids = [short_id(cid) for cid in first_pass.evidence_chunk_ids]
    payload = {
        "status": first_pass.status.value,
        "evidence_chunk_ids": [cid for cid in dict.fromkeys(ids) if cid is not None],
    }
    return render_data_block("first_pass", json.dumps(payload), untrusted=False)


class AgenticVerifier(LLMVerifier):
    variant = Verifier.V4

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]:
        full = vc.sources.full_text
        if full is None:
            return [await self._on_source(vc, vc.sources.abstract)]
        if vc.llm_budget is not None and not await vc.llm_budget():
            return [deterministic(vc.info, UnverifiableReason.PROCESSING_ERROR, self.variant)]
        result = await investigate(self._deps, vc, full, verifier=self.variant, first_pass=None)
        if result.outcome is None:
            return [flagged_error(vc, self.variant, result.agent_run_id)]
        return [result.outcome]
