"""Kiểm tra quote nguyên văn trong chunk (spec §7.8 bước 2).

Model (W5 và A2) thấy văn bản chunk đã qua `escape_data` của data block nên có thể chép
`&lt;`, `&gt;`, `&amp;`; quote được giải escape (`unescape_quote`) trước khi so nguyên văn.
"""

import re
import unicodedata

from citeproof.modules.verification.verdict import Verdict

__all__ = ["locate_quote", "unescape_evidence", "unescape_quote"]

_ENTITY = re.compile(r"&(amp|lt|gt);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">"}


def unescape_quote(quote: str) -> str:
    """Nghịch đảo đúng của `escape_data` (một lượt, nên `&amp;lt;` thành `&lt;`)."""
    return _ENTITY.sub(lambda m: _ENTITIES[m.group(1)], quote)


def unescape_evidence(verdict: Verdict) -> Verdict:
    """Verdict với mọi quote đã giải escape của data block."""
    evidence = [
        item.model_copy(update={"quote": unescape_quote(item.quote)}) for item in verdict.evidence
    ]
    return verdict.model_copy(update={"evidence": evidence})


def _normalize_quote(quote: str) -> str:
    return " ".join(unicodedata.normalize("NFC", quote).split())


def _normalize_with_map(text: str) -> tuple[str, list[int], list[int]]:
    """Chuẩn hóa NFC + gộp khoảng trắng; trả về (chuỗi, đầu gốc, cuối gốc) theo từng ký tự."""
    clusters: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        clusters.append((i, j))
        i = j
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    prev_space = False
    for s, e in clusters:
        piece = text[s:e]
        if piece[0].isspace():
            if out and not prev_space:
                out.append(" ")
                starts.append(s)
                ends.append(e)
            elif prev_space and out:
                ends[-1] = e
            prev_space = True
            continue
        prev_space = False
        for ch in unicodedata.normalize("NFC", piece):
            out.append(ch)
            starts.append(s)
            ends.append(e)
    return "".join(out), starts, ends


def locate_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Vị trí `[start, end)` của quote trong `text` gốc, hoặc None nếu không khớp nguyên văn.

    So khớp sau NFC và gộp khoảng trắng; dấu lược `…`/`...` được so nguyên văn. Ranh giới từ:
    quote bắt đầu (kết thúc) bằng chữ/số thì vị trí khớp không được đứng ngay sau (trước) một
    chữ/số trong `text` ("95.7%" không khớp trong "195.7%"); gặp vị trí như vậy thì tìm tiếp.
    """
    needle = _normalize_quote(quote)
    if not needle:
        return None
    haystack, starts, ends = _normalize_with_map(text)
    check_start = needle[0].isalnum()
    check_end = needle[-1].isalnum()
    pos = haystack.find(needle)
    while pos >= 0:
        end = pos + len(needle)
        if not (check_start and pos > 0 and haystack[pos - 1].isalnum()) and not (
            check_end and end < len(haystack) and haystack[end].isalnum()
        ):
            return starts[pos], ends[end - 1]
        pos = haystack.find(needle, pos + 1)
    return None
