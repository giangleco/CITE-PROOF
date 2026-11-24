"""W1 — parse tài liệu tham khảo bằng LLM (`reference.parse`, spec §7.2, §8.2).

Chỉ gọi cho mục mà GROBID và rule không tìm được tiêu đề lẫn DOI, theo lô tối đa 10 chuỗi;
mỗi chuỗi nằm trong một data block `<reference id="rN" untrusted="true">` riêng. Validator
tất định giữ một trường chỉ khi nó xuất hiện trong chuỗi gốc: tiêu đề (và nơi đăng) có
`partial_ratio` ≥ 90 sau `title_norm` và không dài hơn chuỗi gốc; năm, DOI (đã chuẩn hóa),
ISBN, URL là chuỗi con; tác giả có họ (gấp dấu) xuất hiện. Đầu ra hỏng sau số lần thử tối đa
(`LLMOutputError`) cho kết quả `None` với cả lô. `parse_with_llm_partial` dừng ở lô đầu tiên
gặp `RetryableError` và trả kèm lỗi đó, giữ kết quả các lô đã xong; `parse_with_llm` ném lại
lỗi đó. Lỗi khác (cache phát lại, lỗi lập trình) lan ra ngoài. Không log chuỗi tài liệu tham
khảo hay đầu ra LLM.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from rapidfuzz import fuzz

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import LLMTask, RefType
from citeproof.core.errors import LLMOutputError, RetryableError
from citeproof.llm.client import LLMClient
from citeproof.llm.context import PromptParts, render_data_block
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.types import CallContext, OutputModel
from citeproof.modules.references.normalize import (
    family_name,
    fold,
    nfc,
    nfc_reference,
    normalize_doi,
    title_norm,
)
from citeproof.pipeline.config import TaskSpec

logger = logging.getLogger(__name__)

TASK = LLMTask.REFERENCE_PARSE
BATCH_SIZE = 10
TITLE_MIN_RATIO = 90.0
_ISBN_CHARS = re.compile(r"[^0-9X]")


class ParsedReferenceItem(OutputModel):
    """Trường W1 trả cho một chuỗi; mọi trường bắt buộc (không có giá trị mặc định)."""

    ref_id: str
    title: str | None
    authors: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    isbn: str | None
    url: str | None
    ref_type: RefType
    unparseable: bool


class ParsedReferenceBatch(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "parsed_reference_batch/1"

    items: list[ParsedReferenceItem]


def build_parse_prompt(prompts: PromptLoader, version: str, raws: Sequence[str]) -> PromptParts:
    """Prompt W1 cho 1-10 chuỗi, id `r1…rN` theo thứ tự đầu vào."""
    if not 1 <= len(raws) <= BATCH_SIZE:
        raise ValueError(f"W1 nhận 1..{BATCH_SIZE} chuỗi mỗi lô, có {len(raws)}")
    blocks = "\n\n".join(
        render_data_block("reference", raw, attrs={"id": f"r{n}"}, untrusted=True)
        for n, raw in enumerate(raws, start=1)
    )
    return prompts.render(TASK.value, version, references=blocks, count=len(raws))


def _text_supported(value: str, raw_norm: str) -> bool:
    norm = title_norm(value)
    return (
        bool(norm)
        and len(norm) <= len(raw_norm)
        and fuzz.partial_ratio(norm, raw_norm) >= TITLE_MIN_RATIO
    )


def _isbn_supported(isbn: str, raw: str) -> bool:
    digits = _ISBN_CHARS.sub("", isbn.upper())
    return len(digits) >= 10 and digits in _ISBN_CHARS.sub("", raw.upper())


def keep_supported_fields(item: ParsedReferenceItem, raw: str) -> ParsedReference:
    """Trường W1 có trong chuỗi gốc; trường không đạt bị bỏ (spec §7.2). Kết quả NFC."""
    raw_norm = title_norm(raw)
    raw_fold = fold(nfc(raw))
    doi = normalize_doi(item.doi)
    title = item.title if item.title and _text_supported(item.title, raw_norm) else None
    venue = item.venue if item.venue and _text_supported(item.venue, raw_norm) else None
    authors = [
        author.strip()
        for author in item.authors
        if (family := fold(family_name(nfc(author)))) and family in raw_fold
    ]
    return nfc_reference(
        ParsedReference(
            title=title.strip() if title else None,
            authors=authors,
            year=item.year if item.year is not None and str(item.year) in raw else None,
            venue=venue.strip() if venue else None,
            doi=item.doi.strip() if item.doi and doi is not None and doi in raw_fold else None,
            isbn=item.isbn.strip() if item.isbn and _isbn_supported(item.isbn, raw) else None,
            url=item.url.strip() if item.url and item.url.strip() in raw else None,
        )
    )


W1Result = tuple[ParsedReference, RefType]


@dataclass(frozen=True)
class W1Outcome:
    """Kết quả W1 cùng thứ tự đầu vào, kèm lý do các mục không được W1 parse."""

    results: list[W1Result | None]
    invalid_batches: int  # lô có đầu ra hỏng sau số lần thử tối đa
    unavailable: RetryableError | None  # lỗi tạm thời; lô đó và các lô sau không được gọi


async def parse_with_llm_partial(
    llm: LLMClient,
    prompts: PromptLoader,
    spec: TaskSpec,
    ctx: CallContext,
    raws: Sequence[str],
) -> W1Outcome:
    """W1 theo lô 10; dừng ở `RetryableError` đầu tiên nhưng giữ kết quả các lô đã xong."""
    results: list[W1Result | None] = []
    invalid = 0
    for start in range(0, len(raws), BATCH_SIZE):
        batch = raws[start : start + BATCH_SIZE]
        parts = build_parse_prompt(prompts, spec.prompt_version, batch)
        try:
            result = await llm.complete_structured(
                parts=parts,
                schema=ParsedReferenceBatch,
                model=spec.model,
                ctx=ctx,
                max_tokens=spec.max_tokens,
                effort=spec.effort,
            )
        except LLMOutputError:
            logger.warning("w1_output_invalid", extra={"error_type": "LLMOutputError"})
            results.extend([None] * len(batch))
            invalid += 1
            continue
        except RetryableError as exc:
            results.extend([None] * (len(raws) - start))
            return W1Outcome(results, invalid, exc)
        by_id: dict[str, ParsedReferenceItem] = {}
        for returned in result.value.items:
            by_id.setdefault(returned.ref_id, returned)  # id lặp: giữ mục đầu; id lạ: bỏ qua
        for n, raw in enumerate(batch, start=1):
            item = by_id.get(f"r{n}")
            if item is None or item.unparseable:
                results.append(None)
            else:
                results.append((keep_supported_fields(item, raw), item.ref_type))
    return W1Outcome(results, invalid, None)


async def parse_with_llm(
    llm: LLMClient,
    prompts: PromptLoader,
    spec: TaskSpec,
    ctx: CallContext,
    raws: Sequence[str],
) -> list[W1Result | None]:
    """Kết quả W1 cùng thứ tự `raws`: (trường đã kiểm, loại tài liệu) hoặc `None` khi W1 không
    trả mục hợp lệ (đầu ra hỏng, thiếu id, `unparseable`). Ném lại `RetryableError`.
    """
    outcome = await parse_with_llm_partial(llm, prompts, spec, ctx, raws)
    if outcome.unavailable is not None:
        raise outcome.unavailable
    return outcome.results
