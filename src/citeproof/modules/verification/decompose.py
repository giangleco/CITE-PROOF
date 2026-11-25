"""W4 — tách claim thành ≤4 ý độc lập cho V3 (`claim.decompose`, spec §7.8, §8.2).

`LLMOutputError` (đầu ra sai schema) cho một ý duy nhất là cả claim; các lỗi khác của
`LLMClient` được ném lại cho stage quyết định. Mã (không phải LLM) đặt lại `id` là `s1…sn`
theo thứ tự. Không log văn bản claim hay đầu ra LLM.
"""

import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from citeproof.core.enums import LLMTask
from citeproof.core.errors import LLMOutputError
from citeproof.llm.client import LLMClient
from citeproof.llm.context import render_data_block
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.types import CallContext, OutputModel
from citeproof.pipeline.config import TaskSpec

__all__ = ["SubClaim", "SubClaims", "decompose"]

TASK = LLMTask.CLAIM_DECOMPOSE
logger = logging.getLogger(__name__)


class SubClaim(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=300)


class SubClaims(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "subclaims/1"

    subclaims: list[SubClaim] = Field(min_length=1, max_length=4)


async def decompose(
    llm: LLMClient,
    prompts: PromptLoader,
    spec: TaskSpec,
    ctx: CallContext,
    claim_text: str,
    context_text: str,
) -> list[SubClaim]:
    """Các ý con theo thứ tự (`s1…`); đầu ra không hợp lệ hoặc rỗng → cả claim là `s1`."""
    parts = prompts.render(
        TASK.value,
        spec.prompt_version,
        claim=render_data_block("claim", claim_text, untrusted=True),
        context=render_data_block("context", context_text, untrusted=True),
    )
    fallback = [SubClaim(id="s1", text=claim_text[:300])]
    try:
        result = await llm.complete_structured(
            parts=parts,
            schema=SubClaims,
            model=spec.model,
            ctx=ctx,
            max_tokens=spec.max_tokens,
            effort=spec.effort,
        )
    except LLMOutputError:
        logger.warning("w4_output_invalid", extra={"error_type": "LLMOutputError"})
        return fallback
    texts: list[str] = []
    for item in result.value.subclaims:
        text = item.text.strip()
        if text and text not in texts:
            texts.append(text)
    if not texts:
        return fallback
    return [SubClaim(id=f"s{i}", text=text) for i, text in enumerate(texts, start=1)]
