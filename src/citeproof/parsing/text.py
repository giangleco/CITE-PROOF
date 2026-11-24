"""Đầu vào văn bản (T-205, spec §7.1): JSON `{"body", "references"}` do giao diện lưu.

Thân bài tách đoạn theo dòng trống; danh mục tách mỗi dòng không rỗng thành một
`RawReference`, nhãn đầu dòng `[n]` hoặc `n.` được tách khỏi chuỗi gốc. `references`
là chuỗi (giao diện) hoặc danh sách chuỗi (bộ golden).

`parse_plain_text`: văn bản UTF-8 thuần (nguồn `.txt` của bộ đánh giá ở S6, preflight F10),
đoạn tách theo dòng trống như thân bài, không có danh mục.
"""

import json
import re
from typing import Any

from citeproof.core.errors import ParseError
from citeproof.parsing.model import ParagraphDraft, ParsedDocument, RawReference

_BLANK_LINE = re.compile(r"\n[ \t\f\v]*\n\s*")
# `n.` tối đa 3 chữ số để dòng bắt đầu bằng năm ("2016. …") không bị coi là nhãn.
_LABEL = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,3})\.(?=\s))\s*")


def _invalid() -> ParseError:
    return ParseError("Không đọc được nội dung văn bản đã lưu.", code="TEXT_PAYLOAD_INVALID")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_reference_label(line: str) -> RawReference:
    """Tách nhãn `[n]`/`n.` ở đầu dòng; dòng không có nhãn giữ nguyên, `label=None`."""
    match = _LABEL.match(line)
    if match is None:
        return RawReference(raw=line)
    return RawReference(raw=line[match.end() :], label=match.group(1) or match.group(2))


def _reference_lines(references: Any) -> list[str]:
    if isinstance(references, str):
        text = references
    elif isinstance(references, list) and all(isinstance(item, str) for item in references):
        text = "\n".join(references)
    else:
        raise _invalid()
    return [line for line in _normalize_newlines(text).split("\n") if line.strip()]


def parse_text_payload(data: bytes) -> ParsedDocument:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid() from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
        raise _invalid()
    body = _normalize_newlines(payload["body"])
    drafts = [
        ParagraphDraft(text=part, section=None, page=None) for part in _BLANK_LINE.split(body)
    ]
    references = [
        split_reference_label(line) for line in _reference_lines(payload.get("references", ""))
    ]
    return ParsedDocument.build(
        source="text", drafts=drafts, page_count=None, references_raw=references
    )


def parse_plain_text(data: bytes) -> ParsedDocument:
    """Văn bản UTF-8 (bỏ BOM nếu có) → `ParsedDocument(source="text")`, đoạn theo dòng trống."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("Tệp văn bản không phải UTF-8.", code="TEXT_DECODE_FAILED") from exc
    drafts = [
        ParagraphDraft(text=part, section=None, page=None)
        for part in _BLANK_LINE.split(_normalize_newlines(text))
    ]
    return ParsedDocument.build(source="text", drafts=drafts, page_count=None)
