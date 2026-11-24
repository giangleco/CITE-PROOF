"""Parse DOCX bằng python-docx (T-204, spec §7.1).

Kiểm tra gói zip trước khi mở: có `vbaProject.bin` (macro) → `DOCX_MACRO_NOT_ALLOWED`;
tổng dung lượng giải nén khai báo vượt giới hạn → `FILE_TOO_LARGE` (zipfile không đọc
quá `file_size` khai báo, nên tổng này chặn được zip bomb). Đoạn lấy theo thứ tự trong
thân bài, gồm cả đoạn trong bảng (`region = TABLE`); bỏ đoạn lồng trong hộp văn bản
(có bản sao dự phòng nên sẽ bị lặp). Tiêu đề mục: style tên bắt đầu `Heading`/`Title`
hoặc có mức dàn ý 0-8 (trực tiếp hay kế thừa qua style); style `Caption` là chú thích.
DOCX không có số trang (`page = None`).
"""

import io
import re
import zipfile
import zlib

from docx import Document as open_docx
from docx.document import Document as DocxDocument
from docx.exceptions import PythonDocxError
from docx.opc.exceptions import OpcError
from docx.oxml.exceptions import XmlchemyError
from docx.oxml.ns import qn
from docx.oxml.text.paragraph import CT_P
from docx.oxml.text.parfmt import CT_PPr
from docx.styles.style import ParagraphStyle
from docx.text.paragraph import Paragraph as DocxParagraph

from citeproof.core.enums import ChunkRegion
from citeproof.core.errors import InputError
from citeproof.parsing.model import ParagraphDraft, ParagraphKind, ParsedDocument

MAX_UNCOMPRESSED_BYTES = 200_000_000
_MACRO_PART = "vbaproject.bin"
_HEADING_STYLE_PREFIXES = ("heading", "title")
_CAPTION_STYLE_PREFIX = "caption"
_TABLE_CAPTION = re.compile(r"^\s*(?:bảng|table)\b", re.IGNORECASE)
# Mức dàn ý 0-8 là tiêu đề; 9 là thân bài.
_MAX_HEADING_OUTLINE_LEVEL = 8
_P = qn("w:p")
_TBL = qn("w:tbl")
_OUTLINE_LEVEL = qn("w:outlineLvl")
_VAL = qn("w:val")
# Lỗi python-docx/lxml/zipfile khi gói hỏng (XMLSyntaxError của lxml là SyntaxError;
# dòng nén hỏng ném zlib.error/EOFError, kiểu nén lạ ném NotImplementedError).
_OPEN_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    KeyError,
    ValueError,
    SyntaxError,
    OpcError,
    PythonDocxError,
    XmlchemyError,
)


def _unreadable() -> InputError:
    return InputError(
        "Không đọc được tệp DOCX. Tệp có thể bị hỏng hoặc không phải DOCX hợp lệ.",
        code="UNSUPPORTED_FILE_TYPE",
    )


def _check_archive(data: bytes, max_uncompressed_bytes: int) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise _unreadable() from exc
    if any(info.filename.lower().rsplit("/", 1)[-1] == _MACRO_PART for info in infos):
        raise InputError(
            "Tệp DOCX có chứa macro. Hãy lưu lại thành tệp .docx thường (không macro) rồi tải lên.",
            code="DOCX_MACRO_NOT_ALLOWED",
        )
    if sum(info.file_size for info in infos) > max_uncompressed_bytes:
        raise InputError(
            f"Tệp DOCX khi giải nén vượt quá giới hạn {max_uncompressed_bytes // 1_000_000} MB.",
            code="FILE_TOO_LARGE",
        )


def _outline_level(p_pr: CT_PPr | None) -> int | None:
    if p_pr is None:
        return None
    element = p_pr.find(_OUTLINE_LEVEL)
    if element is None:
        return None
    try:
        return int(element.get(_VAL))
    except (TypeError, ValueError):
        return None


def _style_outline_level(style: ParagraphStyle | None) -> int | None:
    seen: set[str] = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        level = _outline_level(style.element.pPr)
        if level is not None:
            return level
        style = style.base_style
    return None


def _is_heading(element: CT_P, style: ParagraphStyle | None) -> bool:
    name = (style.name or "").lower() if style is not None else ""
    if name.startswith(_HEADING_STYLE_PREFIXES):
        return True
    level = _outline_level(element.pPr)
    if level is None:
        level = _style_outline_level(style)
    return level is not None and 0 <= level <= _MAX_HEADING_OUTLINE_LEVEL


def _has_ancestor(element: CT_P, tag: str) -> bool:
    return any(ancestor.tag == tag for ancestor in element.iterancestors())


def _drafts(document: DocxDocument) -> list[ParagraphDraft]:
    drafts: list[ParagraphDraft] = []
    section: str | None = None
    for element in document.element.body.iter(_P):
        if not isinstance(element, CT_P) or _has_ancestor(element, _P):
            continue
        paragraph = DocxParagraph(element, document)
        text = paragraph.text
        style = paragraph.style
        region = ChunkRegion.TABLE if _has_ancestor(element, _TBL) else ChunkRegion.BODY
        kind: ParagraphKind = "body"
        if _is_heading(element, style):
            kind = "heading"
            if text.strip():
                section = text
        elif style is not None and (style.name or "").lower().startswith(_CAPTION_STYLE_PREFIX):
            kind = "caption"
            region = ChunkRegion.TABLE if _TABLE_CAPTION.match(text) else ChunkRegion.FIGURE
        drafts.append(
            ParagraphDraft(text=text, section=section, page=None, region=region, kind=kind)
        )
    return drafts


def parse_docx(
    data: bytes, *, max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES
) -> ParsedDocument:
    """Đoạn văn và tiêu đề mục của tệp DOCX.

    `InputError` khi tệp bị từ chối (macro, quá lớn), hỏng, hoặc không có chữ nào
    (`NO_TEXT_EXTRACTED`, spec §7.1: không để lần chạy "hoàn tất" với báo cáo rỗng).
    """
    _check_archive(data, max_uncompressed_bytes)
    try:
        document = open_docx(io.BytesIO(data))
    except _OPEN_ERRORS as exc:
        raise _unreadable() from exc
    parsed = ParsedDocument.build(source="docx", drafts=_drafts(document), page_count=None)
    if not parsed.paragraphs:
        raise InputError(
            "Không trích được văn bản từ tệp DOCX. Tệp có thể chỉ chứa hình ảnh.",
            code="NO_TEXT_EXTRACTED",
        )
    return parsed
