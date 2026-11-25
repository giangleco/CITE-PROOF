"""W3 — dịch claim sang tiếng Anh cho truy vấn X2 (`claim.translate`, spec §7.7, §8.2).

Bản dịch làm rơi một con số có trong claim gốc thì bị bỏ (`None`): truy vấn dịch chỉ để tăng
recall, không được đổi nghĩa. Lỗi của `LLMClient` được ném lại cho stage quyết định. Không log
văn bản claim hay đầu ra LLM.
"""

import re
from typing import ClassVar

from citeproof.core.enums import LLMTask
from citeproof.llm.client import LLMClient
from citeproof.llm.context import render_data_block
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.types import CallContext, OutputModel
from citeproof.pipeline.config import TaskSpec

__all__ = ["TranslatedClaim", "numbers_preserved", "translate_claim"]

TASK = LLMTask.CLAIM_TRANSLATE
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


class TranslatedClaim(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "translated_claim/1"

    text_en: str
    kept_terms: list[str]


def numbers_preserved(original: str, translated: str) -> bool:
    """Mọi số của `original` có trong `translated` (sau khi đổi `,` thành `.`)."""
    haystack = translated.replace(",", ".")
    return all(n.replace(",", ".") in haystack for n in _NUMBER.findall(original))


async def translate_claim(
    llm: LLMClient,
    prompts: PromptLoader,
    spec: TaskSpec,
    ctx: CallContext,
    claim_text: str,
) -> str | None:
    """Bản dịch tiếng Anh của claim, hoặc None khi rỗng hoặc làm rơi số của claim gốc."""
    parts = prompts.render(
        TASK.value,
        spec.prompt_version,
        claim=render_data_block("claim", claim_text, untrusted=True),
    )
    result = await llm.complete_structured(
        parts=parts,
        schema=TranslatedClaim,
        model=spec.model,
        ctx=ctx,
        max_tokens=spec.max_tokens,
        effort=spec.effort,
    )
    text = result.value.text_en.strip()
    if not text or not numbers_preserved(claim_text, text):
        return None
    return text
