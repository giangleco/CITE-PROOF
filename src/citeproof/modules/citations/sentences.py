"""Tách câu cho S4 (spec §7.4), theo luật, không dùng thư viện.

Ranh giới câu: dấu kết câu `[.!?…]` (có thể kèm ngoặc/nháy đóng) + khoảng trắng + chữ hoa,
chữ số, ngoặc mở hoặc nháy mở. Không tách sau chữ viết tắt (`et al.`, `e.g.`, `Fig.`, `cs.`,
`TS.`, `tr.`, `v.v.`… — `ABBREVIATIONS`) hay giữa số thập phân (`3.5` không có khoảng trắng nên
không bao giờ là ranh giới). Xuống dòng trong đoạn (PDF) được coi như khoảng trắng.

Marker số đặt ngay sau dấu chấm (`…this.[1] Next`, `…this. [1, tr. 25] Next`) thuộc câu trước
khi theo sau marker là đầu câu mới hoặc hết đoạn; `[4] cho rằng…` vẫn là đầu câu mới. Dấu chấm
trong vị trí trang (`tr.`, `p.`, `pp.`) là viết tắt nên không tách câu giữa marker.

Vị trí `start`/`end` tính trong đoạn: `text[start:end] == sentence.text`; câu đã bỏ khoảng
trắng hai đầu và không bao giờ rỗng.
"""

import re
from dataclasses import dataclass

from citeproof.modules.citations.markers import numeric_marker_numbers

__all__ = ["ABBREVIATIONS", "Sentence", "split_sentences"]

# Viết tắt (chữ thường, kèm dấu chấm cuối) mà sau nó không tách câu. Danh sách của spec §7.4
# cộng vài viết tắt hay gặp trong luận văn (TSKH., TP., VD., cf., p., ed.). Học hàm, học vị ghép
# ("PGS.TS.", "GS.TSKH.") là viết tắt khi mọi phần giữa các dấu chấm đều là viết tắt.
ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "al.", "e.g.", "i.e.", "fig.", "eq.", "vs.", "cs.", "ts.", "ths.", "pgs.", "gs.",
        "tr.", "pp.", "vol.", "no.", "v.v.",
        "tskh.", "tp.", "vd.", "cf.", "p.", "ed.", "eds.",
    }
)  # fmt: skip

_END = re.compile("[.!?\u2026]+[\"'\u201d\u2019\u00bb)\\]]*")
_DASH = "\\-\u2013"  # gạch nối (đã thoát, dùng trong lớp ký tự) và gạch ngang dài
# Ngoặc vuông ngay sau dấu kết câu; là marker số khi `numeric_marker_numbers` nhận phần trong.
_POST_BRACKET = re.compile(rf"\s?(?:[{_DASH}]\s?)?\[([^\[\]]{{1,200}})\]")
_SPACE = re.compile(r"\s+")
_OPENERS = "([\"'\u201c\u2018\u00ab"
_TOKEN_STOP = ",;:([{\"'\u201c\u2018\u00ab"


@dataclass(frozen=True)
class Sentence:
    text: str
    start: int
    end: int


def _opens_sentence(text: str, index: int) -> bool:
    """Ký tự ở `index` mở đầu được một câu: chữ hoa, chữ số, ngoặc hoặc nháy mở."""
    if index >= len(text):
        return False
    char = text[index]
    return char.isupper() or char.isdigit() or char in _OPENERS


def _is_abbreviation(text: str, dot_end: int) -> bool:
    """Từ kết thúc ở `dot_end` (gồm dấu chấm) là viết tắt không kết câu: có trong
    `ABBREVIATIONS`, hoặc mọi phần giữa các dấu chấm đều có ("PGS.TS." → "pgs.", "ts.").
    Từ bắt đầu sau khoảng trắng, ngoặc/nháy mở hoặc `,;:` ("[2,p. 5]" → "p.").
    """
    start = dot_end
    while start > 0 and not text[start - 1].isspace() and text[start - 1] not in _TOKEN_STOP:
        start -= 1
    token = text[start:dot_end].lower()
    if token in ABBREVIATIONS:
        return True
    parts = [part for part in token.split(".") if part]
    return bool(parts) and all(f"{part}." in ABBREVIATIONS for part in parts)


def _boundary(text: str, match: re.Match[str]) -> int | None:
    """Vị trí kết thúc câu (sau dấu kết câu và marker dính sau nó), hoặc None nếu không tách."""
    punct = match.group()
    if punct[0] == "." and _is_abbreviation(text, match.start() + 1):
        return None
    end = match.end()
    # Marker số ngay sau dấu chấm thuộc câu trước nếu sau nó là đầu câu mới hoặc hết đoạn.
    position = end
    while (bracket := _POST_BRACKET.match(text, position)) is not None:
        if numeric_marker_numbers(bracket.group(1)) is None:
            break
        position = bracket.end()
    if position > end:
        rest = _SPACE.match(text, position)
        after = rest.end() if rest is not None else position
        if after >= len(text) or (rest is not None and _opens_sentence(text, after)):
            return position
    space = _SPACE.match(text, end)
    if space is None or not _opens_sentence(text, space.end()):
        return None
    return end


def _trimmed(text: str, start: int, end: int) -> Sentence | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Sentence(text=text[start:end], start=start, end=end) if start < end else None


def split_sentences(text: str) -> list[Sentence]:
    """Các câu của một đoạn, theo thứ tự, với vị trí trong đoạn."""
    sentences: list[Sentence] = []
    start = 0
    for match in _END.finditer(text):
        if match.start() < start:
            continue  # nằm trong marker đã gộp vào câu trước
        end = _boundary(text, match)
        if end is None:
            continue
        sentence = _trimmed(text, start, end)
        if sentence is not None:
            sentences.append(sentence)
        start = end
    last = _trimmed(text, start, len(text))
    if last is not None:
        sentences.append(last)
    return sentences
