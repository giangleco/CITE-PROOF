"""S6 — chia văn bản nguồn đã parse thành chunk (spec §7.6, `07a` §4.8.4; T-405).

- Không chunk nào vượt ranh giới mục: mục mới bắt đầu khi `section` đổi và ở mỗi đoạn tiêu đề.
- Trong một mục, các đoạn liền nhau cùng `region` tạo một khối; mỗi chú thích hình/bảng là
  một khối riêng (`region` TABLE/FIGURE). Abstract là mục `"Abstract"` nên luôn thành chunk
  riêng. Khối chỉ có tiêu đề (mục không có nội dung) và mục danh mục tài liệu tham khảo (văn
  bản `pypdfium2` giữ danh mục trong thân bài; GROBID tách riêng) bị bỏ, để tên các công trình
  được trích không thành bằng chứng khi truy xuất.
- Khối được tách thành câu (`sentence_spans`); câu dài hơn `max_tokens` cắt theo từ. Chunk gom
  các câu liền nhau tới `max_tokens` (ước lượng `estimate_tokens`). Khi đã đủ `min_tokens`,
  chunk dừng ở cuối đoạn văn gần nhất; ngược lại dừng ở câu cuối cùng còn vừa.
- Chunk sau bắt đầu lại ở câu cuối của chunk trước (chồng lấn một câu), trừ khi chunk trước
  chỉ có một câu hoặc câu đó cộng câu kế tiếp đã vượt `max_tokens`. Mỗi chunk luôn có ít nhất
  một câu mới, nên không chunk nào nằm gọn trong chunk trước.
- `text == doc.text()[char_start:char_end]` (đã NFC từ `ParsedDocument.build`), có thể chứa
  `"\\n\\n"` giữa các đoạn; `page` là trang của đoạn chứa đầu chunk; `language` theo
  `source_language` trên chính chunk.

Tách câu theo luật đơn giản (module `sources` không dùng bộ tách câu của `citations`): dấu kết
câu `[.!?…]` (có thể kèm ngoặc/nháy đóng) + khoảng trắng + chữ hoa, chữ số, ngoặc hoặc nháy mở;
không tách sau viết tắt quen thuộc (`et al.`, `Fig.`, `e.g.`…) và chữ cái viết tắt tên (`J.`).
Tách sai chỉ làm lệch điểm cắt chunk, không làm sai vị trí.
"""

import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from citeproof.core.enums import ChunkRegion
from citeproof.parsing.language import source_language
from citeproof.parsing.model import Paragraph, ParsedDocument
from citeproof.parsing.references import is_reference_heading

MIN_TOKENS = 250
MAX_TOKENS = 400
TOKENS_PER_WORD = 1.3

_END = re.compile("[.!?\u2026]+[\"'\u201d\u2019\u00bb)\\]]*")
_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"\S+")
_OPENERS = "([\"'\u201c\u2018\u00ab"
# Viết tắt (chữ thường, kèm dấu chấm) mà sau nó không tách câu.
_ABBREVIATIONS = frozenset(
    {
        "al.", "e.g.", "i.e.", "cf.", "vs.", "fig.", "figs.", "eq.", "eqs.", "tab.", "sec.",
        "ch.", "ref.", "refs.", "p.", "pp.", "vol.", "no.", "approx.", "resp.", "dr.", "prof.",
        "mr.", "mrs.", "ms.", "jr.", "sr.", "st.", "inc.", "ltd.", "co.", "corp.",
        "ts.", "ths.", "pgs.", "gs.", "tr.", "tp.", "vd.",
    }
)  # fmt: skip


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    text: str
    page: int | None
    section: str | None
    char_start: int
    char_end: int
    region: ChunkRegion
    language: str


@dataclass(frozen=True)
class _Unit:
    """Một câu (hoặc một mảnh của câu quá dài), vị trí tính trên `doc.text()`."""

    start: int
    end: int
    words: int
    page: int | None
    paragraph_end: bool


def _estimate(words: int) -> int:
    return math.ceil(words * TOKENS_PER_WORD)


def estimate_tokens(text: str) -> int:
    """Số token ước lượng: `ceil(số từ * 1,3)`, từ tách theo khoảng trắng."""
    return _estimate(len(text.split()))


def _is_abbreviation(text: str, dot_end: int) -> bool:
    """Từ kết thúc ở `dot_end` (gồm dấu chấm) là viết tắt: có trong danh sách, hoặc chỉ gồm
    các chữ cái đơn ("J.", "U.S."). Từ bắt đầu sau khoảng trắng hoặc ngoặc/nháy mở.
    """
    start = dot_end - 1
    while start > 0 and not text[start - 1].isspace() and text[start - 1] not in _OPENERS:
        start -= 1
    token = text[start:dot_end].lower()
    if token in _ABBREVIATIONS:
        return True
    return all(len(part) == 1 and part.isalpha() for part in token[:-1].split("."))


def _trimmed(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Vị trí `[start, end)` các câu của một đoạn, theo thứ tự, đã bỏ khoảng trắng hai đầu.

    Giữa hai câu liền nhau chỉ có khoảng trắng.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _END.finditer(text):
        space = _SPACE.match(text, match.end())
        if space is None or space.end() >= len(text):
            continue
        following = text[space.end()]
        if not (following.isupper() or following.isdigit() or following in _OPENERS):
            continue
        if match.group()[0] == "." and _is_abbreviation(text, match.start() + 1):
            continue
        span = _trimmed(text, start, match.end())
        if span is not None:
            spans.append(span)
        start = space.end()
    last = _trimmed(text, start, len(text))
    if last is not None:
        spans.append(last)
    return spans


def _max_words(max_tokens: int) -> int:
    words = int(max_tokens / TOKENS_PER_WORD)
    while words > 1 and _estimate(words) > max_tokens:
        words -= 1
    return max(words, 1)


def _units(paragraphs: Sequence[Paragraph], max_tokens: int) -> list[_Unit]:
    """Câu của các đoạn; câu dài hơn `max_tokens` cắt thành mảnh liên tiếp theo từ."""
    limit = _max_words(max_tokens)
    units: list[_Unit] = []
    for paragraph in paragraphs:
        pieces: list[tuple[int, int, int]] = []
        for start, end in sentence_spans(paragraph.text):
            words = list(_WORD.finditer(paragraph.text, start, end))
            for first in range(0, len(words), limit):
                window = words[first : first + limit]
                pieces.append((window[0].start(), window[-1].end(), len(window)))
        for index, (start, end, count) in enumerate(pieces):
            units.append(
                _Unit(
                    start=paragraph.char_start + start,
                    end=paragraph.char_start + end,
                    words=count,
                    page=paragraph.page,
                    paragraph_end=index == len(pieces) - 1,
                )
            )
    return units


def _pack(units: Sequence[_Unit], min_tokens: int, max_tokens: int) -> list[tuple[int, int]]:
    """Khoảng `[first, last)` các đơn vị của từng chunk (xem docstring module).

    `fresh` là đơn vị đầu tiên chưa thuộc chunk trước: điểm cắt luôn sau nó, nên mỗi chunk có
    ít nhất một đơn vị mới và không bao giờ chỉ là câu chồng lấn (nằm gọn trong chunk trước).
    """
    ranges: list[tuple[int, int]] = []
    start = fresh = 0
    while start < len(units):
        end = start + 1
        words = units[start].words
        while end < len(units) and _estimate(words + units[end].words) <= max_tokens:
            words += units[end].words
            end += 1
        if end == len(units):
            ranges.append((start, end))
            break
        cut = end  # > fresh: câu chồng lấn chỉ được giữ khi vừa cùng đơn vị mới kế tiếp
        for candidate in range(end, fresh, -1):
            if units[candidate - 1].paragraph_end and _estimate(words) >= min_tokens:
                cut = candidate
                break
            words -= units[candidate - 1].words
        ranges.append((start, cut))
        overlap = cut - 1
        fits = _estimate(units[overlap].words + units[cut].words) <= max_tokens
        start = overlap if overlap > start and fits else cut
        fresh = cut
    return ranges


def _new_block(previous: Paragraph, paragraph: Paragraph) -> bool:
    return (
        paragraph.kind == "heading"
        or paragraph.section != previous.section
        or paragraph.region != previous.region
        or "caption" in (paragraph.kind, previous.kind)
    )


def _keep(block: Sequence[Paragraph]) -> bool:
    section = block[0].section
    if section is not None and is_reference_heading(section):
        return False
    return any(p.kind != "heading" for p in block)


def _blocks(paragraphs: Sequence[Paragraph]) -> Iterator[list[Paragraph]]:
    """Các khối được chia đoạn (xem `_keep`), theo thứ tự tài liệu."""
    block: list[Paragraph] = []
    for paragraph in paragraphs:
        if block and _new_block(block[-1], paragraph):
            if _keep(block):
                yield block
            block = []
        block.append(paragraph)
    if block and _keep(block):
        yield block


def chunk_document(
    doc: ParsedDocument, *, min_tokens: int = MIN_TOKENS, max_tokens: int = MAX_TOKENS
) -> list[ChunkDraft]:
    """Chunk của tài liệu theo thứ tự, `chunk_index` từ 0."""
    if not 0 < min_tokens <= max_tokens:
        raise ValueError("cần 0 < min_tokens <= max_tokens")
    text = doc.text()
    chunks: list[ChunkDraft] = []
    for block in _blocks(doc.paragraphs):
        units = _units(block, max_tokens)
        for first, last in _pack(units, min_tokens, max_tokens):
            start, end = units[first].start, units[last - 1].end
            chunk_text = text[start:end]
            chunks.append(
                ChunkDraft(
                    chunk_index=len(chunks),
                    text=chunk_text,
                    page=units[first].page,
                    section=block[0].section,
                    char_start=start,
                    char_end=end,
                    region=block[0].region,
                    language=source_language(chunk_text),
                )
            )
    return chunks
