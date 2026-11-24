"""Tiêu đề danh mục và tách khối danh mục thành mục, dùng chung cho S1 (PDF) và S2.

Một bộ quy tắc cho mọi nguồn (văn bản pypdfium2 khi GROBID không tách được danh mục, khối
sau tiêu đề danh mục trong DOCX/PDF dự phòng), để S2 và S1 không tách khác nhau (spec §7.2).

Đầu vào là các dòng `(trang, dòng)`; dòng rỗng là ranh giới (dòng trống trong PDF, ranh giới
đoạn DOCX). Dòng chỉ có số (số trang) bị bỏ. Tiêu đề nhóm ("Tiếng Việt", "Tài liệu tiếng
nước ngoài", "Phần tiếng Anh"…, hoặc dòng bên gọi chỉ định qua `group_breaks`, ví dụ đoạn
style Heading) không thành mục: là ranh giới và đặt lại thứ tự nhãn (nhiều luận văn đánh số
lại cho từng nhóm).

- **Có nhãn** khi phần lớn mục có nhãn `[n]`/`n.`: mục mới ở dòng có nhãn lớn hơn nhãn
  trước (dòng `2. pp. 1-9` sau `[3]` là dòng bị ngắt), hoặc ở dòng `[n]` ngay sau ranh giới
  (danh sách đánh số lại); dòng không nhãn nối vào mục trước. Một dòng ngắn có dáng tiêu đề
  (không chữ số, không kết thúc bằng dấu câu) ngay trước danh sách đánh số lại là tiêu đề
  nhóm, không nối vào mục trước. Dòng không nhãn đứng trước nhãn đầu tiên tạo mục không nhãn
  (mỗi nhóm giữa hai ranh giới một mục).
- **Không nhãn** (APA): mục mới sau ranh giới, hoặc khi dòng trước kết thúc mục (dấu chấm,
  URL/DOI) và dòng này mở đầu bằng tên tác giả: "Họ, X." (tên đảo kèm chữ viết tắt) hoặc tên
  người/tổ chức rồi "(năm" / "(n.d." trong 80 ký tự đầu, phần trước năm không có chữ số hay
  dấu hai chấm.

Dòng của một mục nối bằng một khoảng trắng; sau `/` hoặc `-` cuối dòng (URL, từ bị ngắt)
nối liền. Nhãn được tách khỏi chuỗi (`RawReference.label`); trang của mục là trang dòng đầu.
Dòng chỉ có nhãn (`[1]` một mình) không để lại khoảng trắng thừa; mục rỗng bị bỏ.
"""

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Literal

from citeproof.parsing.model import RawReference
from citeproof.parsing.text import split_reference_label

REFERENCE_HEADING = re.compile(
    r"danh muc tai lieu tham khao|tai lieu tham khao|references|bibliography|works cited"
)
APPENDIX_HEADING = re.compile(r"(?:phu luc|appendix|appendices)\b")
MAX_HEADING_CHARS = 100

_WHITESPACE = re.compile(r"\s+")
_HEADING_NUMBER = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVX]{1,6}\.)\s+")
# Tiêu đề nhóm trong danh mục (đã gấp dấu): "Tiếng Việt", "A. Tài liệu tiếng Anh", "Tài liệu
# tiếng nước ngoài", "Tài liệu tham khảo tiếng Anh", "Phần tiếng Việt", "English references".
_GROUP_HEADING = re.compile(
    r"(?:(?:[a-z]|[ivx]{1,4}|\d{1,2})[.)]\s*)?"
    r"(?:(?:phan|muc|nhom) )?"
    r"(?:(?:danh muc )?(?:tai lieu|sach|bai bao)(?: tham khao)? )?"
    r"(?:(?:bang )?tieng (?:nuoc ngoai|viet|anh|phap|nga|trung|duc|nhat|han)"
    r"|nuoc ngoai|trong nuoc)"
    r"|(?:english|vietnamese|foreign)(?: language)?(?: references| sources)?"
)
_BRACKET_LABEL = re.compile(r"\s*\[\d{1,4}\]")
_PAGE_NUMBER_LINE = re.compile(r"\d{1,4}")
_LINK_END = re.compile(r"(?:https?://|doi:\s*|\b10\.\d{4,9}/)\S*$", re.IGNORECASE)
_INITIAL_END = re.compile(r"(?:^|[\s,&(])(?:[^\W\d_]\.-?)+$")
_YEAR_END = re.compile(r"\(\d{4}[a-z]?\)[.,]?$")
_AUTHOR_TOKEN = re.compile(r"[^\W\d_][\w'\u2019-]*[,.]?")  # U+2019: nháy cong trong họ kiểu O'Brien
_INITIAL = re.compile(r"(?:[^\W\d_]\.-?)+,?")
_YEAR_MARK = re.compile(r"\((?:\d{4}[a-z]?|n\.\s?d\.)")
_MAX_AUTHOR_PREFIX = 80
_CONTINUATION_WORDS = frozenset(
    {"in", "trong", "retrieved", "available", "accessed", "truy", "nxb", "nhà", "trường"}
)

ReferenceLine = tuple[int | None, str]
_Kind = Literal["text", "break", "group"]


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt và chữ hoa: NFD, bỏ dấu kết hợp, `đ→d`, `Đ→D`, chữ thường."""
    decomposed = unicodedata.normalize("NFD", text)
    plain = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return plain.replace("đ", "d").replace("Đ", "D").lower()


def _heading_key(line: str) -> str:
    return _WHITESPACE.sub(" ", fold(_HEADING_NUMBER.sub("", line.strip()))).strip().rstrip(" :.")


def is_reference_heading(line: str) -> bool:
    """Dòng tiêu đề danh mục ("Tài liệu tham khảo", "V. REFERENCES:"…), không phân biệt dấu."""
    key = _heading_key(line)
    return len(line) <= MAX_HEADING_CHARS and REFERENCE_HEADING.fullmatch(key) is not None


def is_appendix_heading(line: str) -> bool:
    """Dòng mở đầu phần phụ lục ("Phụ lục A", "Appendix"): khối danh mục dừng ở đây."""
    folded = _WHITESPACE.sub(" ", fold(line)).strip()
    return len(line) <= MAX_HEADING_CHARS and APPENDIX_HEADING.match(folded) is not None


def is_group_heading(line: str) -> bool:
    """Tiêu đề nhóm trong danh mục ("Tiếng Việt", "Tài liệu tiếng Anh"), viết hoa chữ đầu."""
    text = line.strip()
    first = next((ch for ch in text if ch.isalpha()), "")
    return (
        len(text) <= MAX_HEADING_CHARS
        and first.isupper()
        and _GROUP_HEADING.fullmatch(_heading_key(text)) is not None
    )


@dataclass
class _Line:
    page: int | None
    text: str
    kind: _Kind


@dataclass
class _Entry:
    page: int | None
    label: str | None
    parts: list[str] = field(default_factory=list)


def _lines(lines: Sequence[ReferenceLine], group_breaks: Collection[int]) -> list[_Line]:
    out: list[_Line] = []
    for index, (page, raw) in enumerate(lines):
        text = raw.strip()
        if index in group_breaks:
            out.append(_Line(page, "", "group"))
        elif not text:
            out.append(_Line(page, "", "break"))
        elif is_group_heading(text):
            out.append(_Line(page, text, "group"))
        elif not _PAGE_NUMBER_LINE.fullmatch(text):
            out.append(_Line(page, text, "text"))
    return out


def _label_number(text: str) -> int | None:
    label = split_reference_label(text).label
    return int(label) if label is not None else None


def _heading_like(text: str) -> bool:
    first = next((ch for ch in text if ch.isalpha()), "")
    return (
        len(text) <= MAX_HEADING_CHARS
        and first.isupper()
        and text[-1] not in ".,;:"
        and not any(ch.isdigit() for ch in text)
    )


def _heading_before_restart(lines: Sequence[_Line], index: int, last: int, boundary: bool) -> bool:
    """Dòng `index` có dáng tiêu đề và danh sách đánh số lại ngay sau nó (nhãn ≤ nhãn trước):
    nhãn mới là 1, hoặc dòng đứng riêng giữa hai ranh giới. Dòng ngắn không có nhãn mới phía
    sau là dòng bị ngắt của mục trước, được giữ nguyên.
    """
    if not _heading_like(lines[index].text):
        return False
    following = next((line for line in lines[index + 1 :] if line.kind == "text"), None)
    number = _label_number(following.text) if following is not None else None
    if number is None or number > last:
        return False
    standalone = boundary and index + 1 < len(lines) and lines[index + 1].kind != "text"
    return number == 1 or standalone


def _labelled(lines: Sequence[_Line]) -> list[_Entry]:
    entries: list[_Entry] = []
    current: _Entry | None = None  # mục mà dòng không nhãn được nối vào
    last = -1
    boundary = True
    for index, line in enumerate(lines):
        if line.kind != "text":
            boundary = True
            if line.kind == "group":
                current, last = None, -1
            continue
        ref = split_reference_label(line.text)
        number = int(ref.label) if ref.label is not None else None
        restart = boundary and _BRACKET_LABEL.match(line.text) is not None
        if number is not None and (number > last or restart):
            last = number
            current = _Entry(line.page, ref.label, [ref.raw])
            entries.append(current)
        elif number is None and _heading_before_restart(lines, index, last, boundary):
            current, last, boundary = None, -1, True  # tiêu đề nhóm không khớp mẫu
            continue
        elif current is not None and (current.label is not None or not boundary):
            current.parts.append(line.text)
        else:
            current = _Entry(line.page, None, [line.text])
            entries.append(current)
        boundary = False
    return entries


def _ends_entry(line: str) -> bool:
    if _LINK_END.search(line):
        return True
    # Chữ viết tắt tên ("Brown, A.") và "(2016)." ở cuối dòng: mục còn tiếp.
    return line.endswith(".") and not _INITIAL_END.search(line) and not _YEAR_END.search(line)


def _starts_entry(line: str) -> bool:
    """Dòng mở đầu một mục: có dạng tác giả, không phải nơi xuất bản hay tên tạp chí.

    "Hà Nội: NXB…", "TP. Hồ Chí Minh: NXB…" (dấu hai chấm, không có năm) và "Neural
    Computation, 9(8), …" (không có chữ viết tắt tên, không có năm) là dòng tiếp theo.
    """
    tokens = line.split()
    first = tokens[0]
    if not first[0].isupper() or first.rstrip(",.").lower() in _CONTINUATION_WORDS:
        return False
    if _AUTHOR_TOKEN.fullmatch(first) is None or any(":" in token for token in tokens[:2]):
        return False
    if (
        first.endswith(",")
        and len(tokens) > 1
        and tokens[1][0].isupper()
        and _INITIAL.fullmatch(tokens[1]) is not None
    ):
        return True  # "Devlin, J.", "Nguyễn, V. A.", "Chang, M.-W."
    year = _YEAR_MARK.search(line)
    if year is None or year.start() > _MAX_AUTHOR_PREFIX:
        return False
    prefix = line[: year.start()]
    return ":" not in prefix and not any(ch.isdigit() for ch in prefix)


def _unlabelled(lines: Sequence[_Line]) -> list[_Entry]:
    entries: list[_Entry] = []
    previous: str | None = None
    for line in lines:
        if line.kind != "text":
            previous = None
            continue
        if previous is None or (_ends_entry(previous) and _starts_entry(line.text)):
            entries.append(_Entry(line.page, None, [line.text]))
        else:
            entries[-1].parts.append(line.text)
        previous = line.text
    return entries


def join_lines(lines: Sequence[str]) -> str:
    """Nối dòng bị ngắt bằng một khoảng trắng; URL/từ ngắt sau `/` hoặc `-` nối liền.

    Bỏ khoảng trắng hai đầu và dòng rỗng (dòng chỉ có nhãn `[1]` để lại phần rỗng).
    """
    parts = [line.strip() for line in lines if line.strip()]
    text = parts[0] if parts else ""
    for part in parts[1:]:
        text += part if text.endswith(("/", "-")) else f" {part}"
    return text


def split_reference_lines(
    lines: Sequence[ReferenceLine], *, group_breaks: Collection[int] = frozenset()
) -> list[RawReference]:
    """Tách các dòng của khối danh mục thành mục (quy tắc ở docstring module).

    `group_breaks`: chỉ số dòng (trong `lines`) là tiêu đề nhóm do bên gọi xác định; dòng đó
    bị bỏ và thứ tự nhãn bắt đầu lại.
    """
    cleaned = _lines(lines, group_breaks)
    labelled = _labelled(cleaned)
    with_label = sum(entry.label is not None for entry in labelled)
    entries = labelled if with_label * 2 > len(labelled) else _unlabelled(cleaned)
    references = (
        RawReference(raw=join_lines(entry.parts), label=entry.label, page=entry.page)
        for entry in entries
    )
    return [ref for ref in references if ref.raw]


def has_reference_text(raw: str) -> bool:
    """Chuỗi còn nội dung sau khi bỏ nhãn `[n]`/`n.` (dòng chỉ có nhãn thì không)."""
    return bool(split_reference_label(raw).raw.strip())


def join_unlabelled(references: Sequence[RawReference]) -> list[RawReference]:
    """Danh mục đã tách theo dòng (văn bản nhập tay): khi phần lớn mục có nhãn, mục không
    nhãn là dòng bị ngắt và được nối vào mục trước (bỏ trường đã parse của mục đó).
    """
    with_label = sum(ref.label is not None for ref in references)
    if with_label * 2 <= len(references):
        return list(references)
    merged: list[RawReference] = []
    for ref in references:
        if ref.label is None and merged:
            previous = merged[-1]
            merged[-1] = previous.model_copy(
                update={"raw": join_lines([previous.raw, ref.raw]), "parsed": None}
            )
        else:
            merged.append(ref)
    return merged
