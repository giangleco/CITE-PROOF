"""Dựng văn bản abstract từ phản hồi metadata (spec §7.5; preflight F38).

Crossref trả abstract dạng JATS XML; OpenAlex trả chỉ mục đảo `abstract_inverted_index`.
Nằm ở `integrations` vì connector cần nó khi dựng `WorkRecord`; S5 chỉ đọc `works.abstract`.
"""

import html
import re
from collections.abc import Mapping, Sequence

# Tiêu đề mở đầu ("Abstract", "Zusammenfassung"…) là nhãn của khối, không phải nội dung.
_LEADING_TITLE = re.compile(
    r"^\s*<(?:jats:)?title\b[^>]*>.*?</(?:jats:)?title\s*>", re.DOTALL | re.IGNORECASE
)
# Ranh giới đoạn: hết đoạn, hết mục, hết tiêu đề con, ngắt dòng.
_BLOCK_BREAK = re.compile(
    r"</(?:jats:)?(?:p|sec|title|list-item)\s*>|<(?:jats:)?break\s*/?>", re.IGNORECASE
)
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")


def clean_jats(text: str) -> str:
    """Văn bản thuần từ JATS/HTML: bỏ thẻ, giải mã thực thể, đoạn cách nhau bằng dòng trống.

    Thẻ trong dòng (`<jats:italic>`, `<jats:sub>`) bị bỏ mà không chèn khoảng trắng
    ("H<sub>2</sub>O" → "H2O"); khoảng trắng gộp lại trong từng đoạn.
    """
    body = _LEADING_TITLE.sub("", text, count=1)
    paragraphs = (
        _SPACE.sub(" ", html.unescape(_TAG.sub("", block))).strip()
        for block in _BLOCK_BREAK.split(body)
    )
    return "\n\n".join(p for p in paragraphs if p)


def abstract_from_inverted_index(index: Mapping[str, Sequence[int]]) -> str:
    """Dựng lại abstract của OpenAlex: mỗi từ đặt ở các vị trí của nó, nối bằng khoảng trắng.

    Chỉ sắp các vị trí có thật (không cấp phát theo vị trí lớn nhất); vị trí trùng giữ từ gặp
    trước.
    """
    words: dict[int, str] = {}
    for word, positions in index.items():
        for position in positions:
            words.setdefault(position, word)
    return " ".join(words[position] for position in sorted(words))
