"""Context của W5 (`verdict.judge`, `verdict.judge_closed_book`; 07a §4.8.3, spec §8.2).

Phần system cố định (giống nhau giữa mọi cặp, cache được); phần user có metadata reference,
claim, ngữ cảnh và các chunk theo thứ tự xếp hạng (chunk mạnh nhất là `chunk_1`), tất cả là
dữ liệu không tin cậy. Lời nhắc ngôn ngữ đầu ra đứng cuối (FR-45).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from citeproof.core.enums import ContentLevelUsed, LLMTask
from citeproof.llm.context import PromptParts, render_data_block
from citeproof.llm.prompts import PromptLoader
from citeproof.modules.verification.explanations import ExplanationLanguage
from citeproof.modules.verification.rules import ChunkInfo
from citeproof.pipeline.config import TaskSpec

__all__ = [
    "JudgeInput",
    "RefMeta",
    "build_judge_prompt",
    "judge_task",
    "language_name",
    "reference_block",
    "render_chunks",
]

_LANGUAGE_NAMES: dict[ExplanationLanguage, str] = {"vi": "Vietnamese", "en": "English"}


@dataclass(frozen=True)
class RefMeta:
    title: str | None
    authors: tuple[str, ...]
    year: int | None
    venue: str | None


@dataclass(frozen=True)
class JudgeInput:
    ref: RefMeta
    content_level: ContentLevelUsed
    claim_text: str
    claim_language: str
    context_text: str
    chunks: tuple[ChunkInfo, ...]
    explanation_language: ExplanationLanguage


def language_name(language: ExplanationLanguage) -> str:
    """Tên tiếng Anh của ngôn ngữ giải thích cho lời nhắc cuối prompt."""
    return _LANGUAGE_NAMES[language]


def judge_task(*, closed_book: bool) -> LLMTask:
    return LLMTask.VERDICT_JUDGE_CLOSED_BOOK if closed_book else LLMTask.VERDICT_JUDGE


def reference_block(ref: RefMeta, content_level: ContentLevelUsed) -> str:
    lines = [
        f"Title: {ref.title or 'unknown'}",
        f"Authors: {', '.join(ref.authors) if ref.authors else 'unknown'}",
        f"Year: {ref.year if ref.year is not None else 'unknown'}",
        f"Venue: {ref.venue or 'unknown'}",
    ]
    return render_data_block(
        "reference", "\n".join(lines), attrs={"content_level": content_level.value}
    )


def _attr_value(value: str) -> str:
    """Bỏ ký tự điều khiển (trừ xuống dòng, tab) mà `render_data_block` từ chối trong thuộc tính:
    tên mục của PDF dự phòng lấy nguyên dòng từ pdfium."""
    return "".join(ch for ch in value if ord(ch) >= 0x20 or ch in "\n\r\t").strip()


def render_chunks(chunks: Sequence[ChunkInfo]) -> str:
    """`<source_chunks untrusted="true">` chứa từng `<chunk id=… page=… section=… region=…>`."""
    blocks = []
    for chunk in chunks:
        attrs = {"id": chunk.short_id}
        if chunk.page is not None:
            attrs["page"] = str(chunk.page)
        section = _attr_value(chunk.section or "")
        if section:
            attrs["section"] = section
        attrs["region"] = chunk.region.value
        blocks.append(render_data_block("chunk", chunk.text, attrs=attrs, untrusted=False))
    return '<source_chunks untrusted="true">\n' + "\n".join(blocks) + "\n</source_chunks>"


def build_judge_prompt(
    prompts: PromptLoader, spec: TaskSpec, inp: JudgeInput, *, closed_book: bool
) -> PromptParts:
    if closed_book and inp.chunks:
        raise ValueError("prompt closed-book không nhận chunk")
    variables = {
        "reference": reference_block(inp.ref, inp.content_level),
        "claim": render_data_block("claim", inp.claim_text, attrs={"lang": inp.claim_language}),
        "context": render_data_block("context", inp.context_text),
        "language": language_name(inp.explanation_language),
    }
    if not closed_book:
        variables["chunks"] = render_chunks(inp.chunks)
    return prompts.render(
        judge_task(closed_book=closed_book).value, spec.prompt_version, **variables
    )
