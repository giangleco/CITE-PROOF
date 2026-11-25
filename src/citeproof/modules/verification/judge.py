"""W5 — một lời gọi `verdict.judge` (hoặc closed-book) cho một cặp claim-reference (spec §7.8).

Lỗi của `LLMClient` được ném lại cho stage quyết định. Quote được giải escape của data block
(`&lt;`, `&gt;`, `&amp;`) trước khi kiểm tra nguyên văn: model thấy chunk đã escape và có thể
chép nguyên dạng đó. Không log prompt hay đầu ra LLM.
"""

from dataclasses import dataclass

from citeproof.llm.client import LLMClient
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.types import CallContext
from citeproof.modules.verification.context import JudgeInput, build_judge_prompt
from citeproof.modules.verification.quotes import unescape_evidence, unescape_quote
from citeproof.modules.verification.verdict import Verdict
from citeproof.pipeline.config import TaskSpec

__all__ = ["JudgeResult", "judge", "unescape_quote"]


@dataclass(frozen=True)
class JudgeResult:
    verdict: Verdict
    model: str
    prompt_version: str


async def judge(
    llm: LLMClient,
    prompts: PromptLoader,
    spec: TaskSpec,
    ctx: CallContext,
    inp: JudgeInput,
    *,
    closed_book: bool = False,
) -> JudgeResult:
    parts = build_judge_prompt(prompts, spec, inp, closed_book=closed_book)
    result = await llm.complete_structured(
        parts=parts,
        schema=Verdict,
        model=spec.model,
        ctx=ctx,
        max_tokens=spec.max_tokens,
        effort=spec.effort,
    )
    return JudgeResult(
        verdict=unescape_evidence(result.value),
        model=result.model or spec.model,
        prompt_version=parts.prompt_version,
    )
