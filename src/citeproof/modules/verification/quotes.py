"""Kiểm tra quote nguyên văn trong chunk (spec §7.8 bước 2)."""

import unicodedata

__all__ = ["locate_quote"]


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
            elif prev_space:
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

    So khớp sau NFC và gộp khoảng trắng; dấu lược `…`/`...` được so nguyên văn.
    """
    needle = _normalize_quote(quote)
    if not needle:
        return None
    haystack, starts, ends = _normalize_with_map(text)
    pos = haystack.find(needle)
    if pos < 0:
        return None
    return starts[pos], ends[pos + len(needle) - 1]
