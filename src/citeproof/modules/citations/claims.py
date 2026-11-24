"""Claim tất định cho mỗi marker (spec §7.4, QĐ-16) và bản nháp claim của S4.

**Span (QĐ-16):** đoạn liền mạch của câu từ cuối marker trước (hoặc đầu câu) đến đầu marker
hiện tại, bỏ khoảng trắng và dấu câu (`.,;:!?…` và gạch ngang) hai đầu. Ngoặc hay nháy mở
chưa đóng trong đoạn đó (marker nằm trong ngoặc: "(xem phụ lục [8])") hoặc ngoặc đóng thừa
(phần sau một ngoặc chứa marker trước) cắt đoạn ngay sau nó, để claim không mở hay đóng ngoặc
dở. Dưới `MIN_CLAIM_WORDS` từ thì lấy cả câu, bỏ marker ở đầu và cuối (lặp lại cùng dấu câu
và ngoặc thừa ở hai đầu); marker nằm giữa được giữ nguyên. Câu chỉ có marker thì claim là
câu bỏ dấu câu hai đầu (không bao giờ rỗng).

`claim_text == sentence.text[span_start:span_end]` luôn đúng (T-402): span tính trên chính
chuỗi câu và không bao giờ sửa chữ.

**Bản nháp:** mỗi marker một `ClaimDraft` kèm danh sách mục được link (bỏ trùng, giữ thứ tự,
nhiều `None` gộp thành một: khóa duy nhất của S8, preflight F41). Bỏ đoạn thuộc khối danh mục
(cùng hàm của S2), tiêu đề và chú thích. `language` là ngôn ngữ của câu, không xác định thì
lấy của đoạn; `context_text` là câu trước + câu sau.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from citeproof.core.enums import Language
from citeproof.modules.citations.linking import ReferenceIndex
from citeproof.modules.citations.markers import Marker, find_markers
from citeproof.modules.citations.sentences import Sentence, split_sentences
from citeproof.modules.references.sections import reference_paragraph_indices
from citeproof.parsing.language import detect_language
from citeproof.parsing.model import Paragraph, ParsedDocument

__all__ = [
    "MIN_CLAIM_WORDS",
    "RULE_METHOD",
    "ClaimDraft",
    "build_drafts",
    "context_text",
    "deterministic_span",
]

MIN_CLAIM_WORDS = 3
RULE_METHOD = "rule"

_EDGE = frozenset(".,;:!?\u2026-\u2013\u2014")  # cả dấu ba chấm và gạch ngang dài
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}", "\u201c": "\u201d"}
_CLOSE_TO_OPEN = {close: open_ for open_, close in _OPEN_TO_CLOSE.items()}
_QUOTE = '"'


@dataclass
class ClaimDraft:
    marker: Marker
    sentence: Sentence
    paragraph_index: int
    span_start: int
    span_end: int
    claim_text: str
    is_claim: bool
    extraction_method: str
    language: str
    context_text: str


def _strip(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and (text[start].isspace() or text[start] in _EDGE):
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in _EDGE):
        end -= 1
    return start, end


def _words(text: str) -> int:
    return sum(1 for token in text.split() if any(char.isalnum() for char in token))


def _after_unbalanced(text: str, start: int, end: int) -> int:
    """Vị trí ngay sau ngoặc/nháy không cân cuối cùng trong `text[start:end]` (hoặc `start`)."""
    cut = start
    opened: list[int] = []
    quotes: list[int] = []
    for index in range(start, end):
        char = text[index]
        if char in _OPEN_TO_CLOSE:
            opened.append(index)
        elif char in _CLOSE_TO_OPEN:
            if opened and text[opened[-1]] == _CLOSE_TO_OPEN[char]:
                opened.pop()
            else:
                cut, opened = index + 1, []
        elif char == _QUOTE:
            quotes.append(index)
    if opened:
        cut = max(cut, opened[-1] + 1)
    if len(quotes) % 2:
        cut = max(cut, quotes[-1] + 1)
    return cut


def _trim_edge_brackets(text: str, start: int, end: int) -> tuple[int, int]:
    """Bỏ ngoặc mở ở đầu không có ngoặc đóng tương ứng, và ngoặc đóng ở cuối không có ngoặc mở."""
    while start < end:
        first, last = text[start], text[end - 1]
        if first in _OPEN_TO_CLOSE and _OPEN_TO_CLOSE[first] not in text[start + 1 : end]:
            start, end = _strip(text, start + 1, end)
        elif last in _CLOSE_TO_OPEN and _CLOSE_TO_OPEN[last] not in text[start : end - 1]:
            start, end = _strip(text, start, end - 1)
        else:
            break
    return start, end


def _sentence_span(sentence: str, markers: Sequence[Marker]) -> tuple[int, int]:
    """Cả câu bỏ marker, dấu câu và ngoặc thừa ở hai đầu; câu chỉ có marker giữ marker."""
    start, end = _strip(sentence, 0, len(sentence))
    changed = True
    while changed and start < end:
        changed = False
        for marker in markers:
            if marker.start == start and marker.end <= end:
                start, end = _strip(sentence, marker.end, end)
                changed = True
            elif marker.end == end and marker.start >= start:
                start, end = _strip(sentence, start, marker.start)
                changed = True
        trimmed = _trim_edge_brackets(sentence, start, end)
        changed = changed or trimmed != (start, end)
        start, end = trimmed
    if start < end:
        return start, end
    start, end = _strip(sentence, 0, len(sentence))
    return (start, end) if start < end else (0, len(sentence))


def deterministic_span(
    sentence: str, marker: Marker, prev_marker_end: int | None
) -> tuple[int, int]:
    """Span claim của `marker` trong câu theo QĐ-16 (vị trí tính trong câu)."""
    region_start = prev_marker_end if prev_marker_end is not None else 0
    cut = _after_unbalanced(sentence, region_start, marker.start)
    start, end = _strip(sentence, cut, marker.start)
    if _words(sentence[start:end]) >= MIN_CLAIM_WORDS:
        return start, end
    return _sentence_span(sentence, find_markers(sentence))


def context_text(sentences: Sequence[Sentence], index: int) -> str:
    """Câu trước và câu sau câu `index`, nối bằng một khoảng trắng ("" khi không có)."""
    around = [
        sentences[position].text
        for position in (index - 1, index + 1)
        if 0 <= position < len(sentences)
    ]
    return " ".join(around)


def _unique(links: Sequence[UUID | None]) -> list[UUID | None]:
    """Bỏ mục trùng (kể cả nhiều `None`), giữ thứ tự xuất hiện (preflight F41)."""
    return list(dict.fromkeys(links))


def _language(sentence: Sentence, paragraph: Paragraph) -> str:
    language = detect_language(sentence.text)
    return (paragraph.language if language is Language.UNKNOWN else language).value


def _paragraph_drafts(
    paragraph: Paragraph, index: ReferenceIndex
) -> list[tuple[ClaimDraft, list[UUID | None]]]:
    drafts: list[tuple[ClaimDraft, list[UUID | None]]] = []
    sentences = split_sentences(paragraph.text)
    for position, sentence in enumerate(sentences):
        markers = find_markers(sentence.text)
        if not markers:
            continue
        language = _language(sentence, paragraph)
        context = context_text(sentences, position)
        previous: int | None = None
        for marker in markers:
            start, end = deterministic_span(sentence.text, marker, previous)
            previous = marker.end
            draft = ClaimDraft(
                marker=marker,
                sentence=sentence,
                paragraph_index=paragraph.index,
                span_start=start,
                span_end=end,
                claim_text=sentence.text[start:end],
                is_claim=True,
                extraction_method=RULE_METHOD,
                language=language,
                context_text=context,
            )
            drafts.append((draft, _unique(index.link(marker))))
    return drafts


def _drafts(
    doc: ParsedDocument, index: ReferenceIndex
) -> list[tuple[ClaimDraft, list[UUID | None]]]:
    skipped = reference_paragraph_indices(doc)
    drafts: list[tuple[ClaimDraft, list[UUID | None]]] = []
    for paragraph in doc.paragraphs:
        if paragraph.index in skipped or paragraph.kind != "body":
            continue
        drafts.extend(_paragraph_drafts(paragraph, index))
    return drafts


async def build_drafts(
    doc: ParsedDocument, index: ReferenceIndex
) -> list[tuple[ClaimDraft, list[UUID | None]]]:
    """Bản nháp claim cho mọi marker của tài liệu, theo thứ tự trong bài (chạy trong thread:
    tách câu cả luận văn là việc nặng CPU).
    """
    return await asyncio.to_thread(_drafts, doc, index)
