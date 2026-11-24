"""W2 — tinh chỉnh span claim bằng LLM (`claim.extract`, spec §7.4, §8.2; T-402).

Một lời gọi cho mỗi đoạn văn có marker: các câu của đoạn nằm trong một data block
`<paragraph untrusted="true">` với con `<s i="N">`, marker là `m1…mn` theo thứ tự xuất hiện.
Validator tất định giữ một mục chỉ khi `0 <= span_start < span_end <= len(câu)` và
`claim_text == câu[span_start:span_end]`; mục hỏng hoặc thiếu để nguyên claim rule (QĐ-16),
nên span luôn là chuỗi con của câu. `is_claim = false` chỉ đặt cờ, giữ span rule. Không log
văn bản bài hay đầu ra LLM.
"""

import dataclasses
from collections.abc import Sequence
from typing import ClassVar

from citeproof.core.enums import LLMTask
from citeproof.llm.client import LLMClient
from citeproof.llm.context import PromptParts, escape_data, render_data_block
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.types import CallContext, OutputModel
from citeproof.modules.citations.claims import ClaimDraft
from citeproof.pipeline.config import TaskSpec

__all__ = [
    "LLM_METHOD",
    "ClaimExtractionBatch",
    "ClaimExtractionItem",
    "apply_extraction",
    "build_extract_prompt",
    "refine_paragraph",
]

TASK = LLMTask.CLAIM_EXTRACT
LLM_METHOD = "llm:claim.extract/v1"


class ClaimExtractionItem(OutputModel):
    """Span W2 trả cho một marker; mọi trường bắt buộc, offset tính trong chuỗi câu."""

    marker_id: str
    is_claim: bool
    span_start: int
    span_end: int
    claim_text: str


class ClaimExtractionBatch(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "claim_extraction_batch/1"

    items: list[ClaimExtractionItem]


def build_extract_prompt(
    prompts: PromptLoader,
    version: str,
    paragraph_sentences: Sequence[str],
    markers: Sequence[tuple[str, int, str]],
) -> PromptParts:
    """Prompt W2 cho một đoạn; `markers` là `(marker_id, sentence_index, marker_text)`."""
    if not paragraph_sentences or not markers:
        raise ValueError("W2 cần ít nhất một câu và một marker")
    sentences = "\n".join(
        f'<s i="{index}">{escape_data(text)}</s>' for index, text in enumerate(paragraph_sentences)
    )
    paragraph = f'<paragraph untrusted="true">\n{sentences}\n</paragraph>'
    marker_blocks = "\n".join(
        render_data_block(
            "marker", text, attrs={"id": marker_id, "sentence": str(index)}, untrusted=True
        )
        for marker_id, index, text in markers
    )
    return prompts.render(
        TASK.value, version, paragraph=paragraph, markers=marker_blocks, count=len(markers)
    )


def apply_extraction(
    batch: ClaimExtractionBatch, drafts: Sequence[ClaimDraft], ids: Sequence[str]
) -> list[ClaimDraft]:
    """Áp kết quả W2 lên `drafts` (`ids[i]` là id marker của `drafts[i]`); xem docstring module."""
    by_id: dict[str, ClaimExtractionItem] = {}
    for item in batch.items:
        by_id.setdefault(item.marker_id, item)  # id lặp: giữ mục đầu; id lạ: bỏ qua
    out: list[ClaimDraft] = []
    for draft, marker_id in zip(drafts, ids, strict=True):
        found = by_id.get(marker_id)
        if found is None:
            out.append(draft)
            continue
        item = found
        text = draft.sentence.text
        valid = (
            0 <= item.span_start < item.span_end <= len(text)
            and item.claim_text == text[item.span_start : item.span_end]
        )
        if valid:
            draft = dataclasses.replace(
                draft,
                span_start=item.span_start,
                span_end=item.span_end,
                claim_text=item.claim_text,
                extraction_method=LLM_METHOD,
            )
        if not item.is_claim:
            draft = dataclasses.replace(draft, is_claim=False)
        out.append(draft)
    return out


async def refine_paragraph(
    llm: LLMClient,
    prompts: PromptLoader,
    spec: TaskSpec,
    ctx: CallContext,
    sentences: Sequence[str],
    drafts: Sequence[tuple[ClaimDraft, int]],
) -> list[ClaimDraft]:
    """W2 cho một đoạn: `drafts` là `(bản nháp, chỉ số câu trong đoạn)` theo thứ tự marker.

    Ném lại mọi lỗi của `LLMClient` (`LLMOutputError`, `RetryableError`…) để stage quyết
    định giữ claim rule hay thử lại.
    """
    ids = [f"m{n}" for n in range(1, len(drafts) + 1)]
    parts = build_extract_prompt(
        prompts,
        spec.prompt_version,
        sentences,
        [
            (marker_id, index, d.marker.text)
            for marker_id, (d, index) in zip(ids, drafts, strict=True)
        ],
    )
    result = await llm.complete_structured(
        parts=parts,
        schema=ClaimExtractionBatch,
        model=spec.model,
        ctx=ctx,
        max_tokens=spec.max_tokens,
        effort=spec.effort,
    )
    return apply_extraction(result.value, [d for d, _ in drafts], ids)
