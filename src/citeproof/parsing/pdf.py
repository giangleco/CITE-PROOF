"""PDF: GROBID là chính, `pypdfium2` dự phòng (ADR-004; spec §7.1, QĐ-03, QĐ-22).

`pypdfium2` (Apache-2.0/BSD, không dùng PyMuPDF vì AGPL) dùng để đếm trang, phát hiện
trang scan (< 25 ký tự không phải khoảng trắng), đọc metadata và trích văn bản khi
không dùng được GROBID. PDFium không an toàn đa luồng: mọi lời gọi đi qua một khóa
chung, kể cả khi chạy trong `asyncio.to_thread`.

Văn bản dự phòng: đoạn tách theo dòng trống trên từng trang, các dòng trong một đoạn
giữ xuống dòng `"\\n"` (S2 tách mục danh mục theo nhãn đầu dòng). Dòng tiêu đề:

- tiêu đề đánh số `^\\d+(\\.\\d+)*\\.?\\s+\\S` (mỗi cấp tối đa hai chữ số, để không nhận nhầm
  năm ở đầu dòng) hoặc số La Mã từ I tới XXXIX có dấu chấm (kiểu IEEE); tối đa 100 ký tự, chữ
  đầu viết hoa, không kết thúc bằng `.,;:` (mục liệt kê, câu bị ngắt dòng);
- tiêu đề danh mục ("Tài liệu tham khảo", "References"…) đứng riêng một dòng;
- sau tiêu đề danh mục cuối cùng chỉ còn "Phụ lục"/"Appendix" là tiêu đề, để mục danh
  mục đánh số `1. Tác giả…` không bị nhận nhầm.

GROBID trả `listBibl` rỗng (thường gặp với danh mục APA tiếng Việt): thân bài, mục và
trang vẫn theo GROBID, còn `references_raw` lấy từ khối sau tiêu đề danh mục cuối cùng
trong văn bản pypdfium2 (cùng quy tắc tiêu đề), mỗi mục một `RawReference` chưa có trường
parse, để S2 xử lý như danh mục DOCX/văn bản (`processCitationList`, rồi rule).
"""

import asyncio
import logging
import re
import threading
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from citeproof.core.errors import InputError, ParseError, RetryableError
from citeproof.integrations.grobid import GrobidClient
from citeproof.parsing.model import ParagraphDraft, ParsedDocument, RawReference
from citeproof.parsing.tei import parse_fulltext_tei
from citeproof.parsing.text import split_reference_label

logger = logging.getLogger(__name__)

SCANNED_PAGES = "SCANNED_PAGES"
PDF_TEXT_FALLBACK = "PDF_TEXT_FALLBACK"
SCANNED_MIN_CHARS = 25
NO_TEXT_MESSAGE = "Không trích được văn bản từ tệp PDF. Tệp có thể là ảnh quét."

_PDFIUM_LOCK = threading.Lock()
_ENCRYPTED_ERRORS = frozenset({pdfium_c.FPDF_ERR_PASSWORD, pdfium_c.FPDF_ERR_SECURITY})
_MAX_HEADING_CHARS = 100
_NUMBERED_HEADING = re.compile(
    r"^(?:\d{1,2}(?:\.\d{1,2})*\.?|(?=[IVX])X{0,3}(?:IX|IV|V?I{0,3})\.)\s+(?P<title>\S.*)$"
)
_REFERENCE_HEADING = re.compile(
    r"(?:danh muc )?tai lieu tham khao|references|bibliography|works cited"
)
_APPENDIX_HEADING = re.compile(r"(?:phu luc|appendix|appendices)\b")
_HEADING_NUMBER = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVX]{1,6}\.)\s+")
_WHITESPACE = re.compile(r"\s+")
# PDFium đánh dấu gạch nối mềm ở cuối dòng bằng U+0002.
_SOFT_HYPHEN_MARK = "\x02"
# Tách mục danh mục không nhãn: mục mới khi dòng trước kết thúc mục (dấu chấm, URL/DOI) và
# dòng này mở đầu bằng tên tác giả ("Họ," hoặc hai từ viết hoa liền nhau, hay "Tên. (năm)").
_PAGE_NUMBER_LINE = re.compile(r"\d{1,4}")
_LINK_END = re.compile(r"(?:https?://|doi:\s*|\b10\.\d{4,9}/)\S*$", re.IGNORECASE)
_INITIAL_END = re.compile(r"(?:^|[\s,&(])(?:[^\W\d_]\.-?)+$")
_YEAR_END = re.compile(r"\(\d{4}[a-z]?\)[.,]?$")
_AUTHOR_TOKEN = re.compile(r"[^\W\d_][\w'\u2019-]*[,.]?")  # U+2019: nháy cong trong họ kiểu O'Brien
_CONTINUATION_WORDS = frozenset(
    {"in", "trong", "retrieved", "available", "accessed", "truy", "nxb", "nhà", "trường"}
)


@contextmanager
def _open(data: bytes) -> Iterator[Any]:
    """Mở PDF dưới khóa PDFium; lỗi mở tệp thành `InputError` tiếng Việt."""
    with _PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if exc.err_code in _ENCRYPTED_ERRORS:
                raise InputError(
                    "Tệp PDF được bảo vệ bằng mật khẩu. Hãy gỡ mật khẩu rồi tải lên lại.",
                    code="PDF_ENCRYPTED",
                ) from exc
            raise InputError(
                "Không đọc được tệp PDF. Tệp có thể bị hỏng hoặc không phải PDF hợp lệ.",
                code="UNSUPPORTED_FILE_TYPE",
            ) from exc
        try:
            yield pdf
        finally:
            pdf.close()


def _clean_page_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(_SOFT_HYPHEN_MARK, "-")
    return "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in {"Cc", "Cs", "Cn"}
    )


def _page_text(pdf: Any, index: int) -> str:
    try:
        page = pdf[index]
    except pdfium.PdfiumError:
        return ""  # trang hỏng: coi như không có chữ (sẽ bị báo là trang scan)
    try:
        textpage = page.get_textpage()
        try:
            return _clean_page_text(str(textpage.get_text_range()))
        finally:
            textpage.close()
    except pdfium.PdfiumError:
        return ""
    finally:
        page.close()


def pdf_page_count(data: bytes) -> int:
    with _open(data) as pdf:
        return len(pdf)


def pdf_page_texts(data: bytes, *, max_pages: int | None = None) -> list[str]:
    """Văn bản từng trang (xuống dòng `"\\n"`), tối đa `max_pages` trang đầu."""
    with _open(data) as pdf:
        count = len(pdf) if max_pages is None else min(len(pdf), max_pages)
        return [_page_text(pdf, index) for index in range(count)]


def pdf_metadata_title(data: bytes) -> str | None:
    with _open(data) as pdf:
        title = str(pdf.get_metadata_dict(skip_empty=True).get("Title") or "").strip()
    return title or None


def _visible_chars(text: str) -> int:
    return sum(not ch.isspace() for ch in text)


def scanned_pages(page_texts: Sequence[str], *, min_chars: int = SCANNED_MIN_CHARS) -> list[int]:
    """Số trang (từ 1) có ít hơn `min_chars` ký tự không phải khoảng trắng."""
    return [
        number
        for number, text in enumerate(page_texts, start=1)
        if _visible_chars(text) < min_chars
    ]


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return _WHITESPACE.sub(" ", plain).strip().lower()


def _is_reference_heading(line: str) -> bool:
    title = _fold(_HEADING_NUMBER.sub("", line)).rstrip(" :")
    return len(line) <= _MAX_HEADING_CHARS and _REFERENCE_HEADING.fullmatch(title) is not None


def _is_appendix_heading(line: str) -> bool:
    return len(line) <= _MAX_HEADING_CHARS and _APPENDIX_HEADING.match(_fold(line)) is not None


def _is_numbered_heading(line: str) -> bool:
    if len(line) > _MAX_HEADING_CHARS or line[-1] in ".,;:":
        return False
    match = _NUMBERED_HEADING.match(line)
    if match is None:
        return False
    first_letter = next((ch for ch in match.group("title") if ch.isalpha()), "")
    return first_letter.isupper()


def _page_lines(page_texts: Sequence[str]) -> list[list[str]]:
    return [[line.strip() for line in text.split("\n")] for text in page_texts]


def _last_reference_heading(pages: Sequence[Sequence[str]]) -> tuple[int, int] | None:
    """(trang từ 0, dòng) của tiêu đề danh mục cuối cùng; như S2, bỏ qua dòng ở mục lục."""
    return max(
        (
            (page, row)
            for page, lines in enumerate(pages)
            for row, line in enumerate(lines)
            if line and _is_reference_heading(line)
        ),
        default=None,
    )


def document_from_page_texts(page_texts: Sequence[str]) -> ParsedDocument:
    """Văn bản từng trang → `ParsedDocument(source="pdfium")` (mục từ dòng tiêu đề)."""
    pages = _page_lines(page_texts)
    last_reference = _last_reference_heading(pages)

    drafts: list[ParagraphDraft] = []
    section: str | None = None
    in_references = False
    buffer: list[str] = []

    def flush(page_number: int) -> None:
        if buffer:
            drafts.append(ParagraphDraft(text="\n".join(buffer), section=section, page=page_number))
            buffer.clear()

    for page, lines in enumerate(pages):
        for row, line in enumerate(lines):
            if not line:
                flush(page + 1)
                continue
            if (page, row) == last_reference:
                in_references = True
                heading = True
            elif in_references:
                heading = _is_appendix_heading(line)
                in_references = not heading
            else:
                heading = _is_reference_heading(line) or _is_numbered_heading(line)
            if heading:
                flush(page + 1)
                section = line
                drafts.append(
                    ParagraphDraft(text=line, section=section, page=page + 1, kind="heading")
                )
            else:
                buffer.append(line)
        flush(page + 1)
    return ParsedDocument.build(
        source="pdfium", drafts=drafts, page_count=len(page_texts), references_raw=None
    )


def parse_pdf_fallback(data: bytes) -> ParsedDocument:
    return document_from_page_texts(pdf_page_texts(data))


def _ends_entry(line: str) -> bool:
    if _LINK_END.search(line):
        return True
    # Chữ viết tắt tên ("Brown, A.") và "(2016)." ở cuối dòng: mục còn tiếp.
    return line.endswith(".") and not _INITIAL_END.search(line) and not _YEAR_END.search(line)


def _starts_entry(line: str) -> bool:
    tokens = line.split()
    first = tokens[0]
    if not first[0].isupper() or first.rstrip(",.").lower() in _CONTINUATION_WORDS:
        return False
    if _AUTHOR_TOKEN.fullmatch(first) is None:
        return False
    if first.endswith(","):
        return True
    return len(tokens) > 1 and (tokens[1][0].isupper() or tokens[1].startswith("("))


def _join_lines(lines: Sequence[str]) -> str:
    """Nối dòng bị ngắt bằng một khoảng trắng; URL/từ ngắt sau `/` hoặc `-` nối liền."""
    text = lines[0]
    for line in lines[1:]:
        text += line if text.endswith(("/", "-")) else f" {line}"
    return text


def _split_entries(block: Sequence[tuple[int, str]]) -> list[RawReference]:
    """Tách khối danh mục (trang, dòng) thành mục.

    Danh mục có nhãn (dòng đầu là `[n]`/`n.`): mục mới ở dòng có nhãn lớn hơn nhãn trước;
    nhãn được tách khỏi chuỗi. Không nhãn: mục mới sau dòng trống, hoặc khi dòng trước kết
    thúc mục và dòng này mở đầu bằng tên tác giả. Dòng chỉ có số (số trang) bị bỏ.
    """
    lines = [(page, line) for page, line in block if not _PAGE_NUMBER_LINE.fullmatch(line)]
    first = next((line for _, line in lines if line), None)
    if first is None:
        return []
    labelled = split_reference_label(first).label is not None
    entries: list[tuple[int, str | None, list[str]]] = []
    last_number = 0
    previous: str | None = None
    for page, line in lines:
        if not line:
            previous = None
            continue
        if labelled:
            ref = split_reference_label(line)
            number = int(ref.label) if ref.label is not None else 0
            if number > last_number or not entries:
                last_number = number
                entries.append((page, ref.label, [ref.raw]))
            else:
                entries[-1][2].append(line)
        elif previous is None or (_ends_entry(previous) and _starts_entry(line)):
            entries.append((page, None, [line]))
        else:
            entries[-1][2].append(line)
        previous = line
    return [
        RawReference(raw=_join_lines(parts), label=label, page=page)
        for page, label, parts in entries
    ]


def reference_entries_from_page_texts(page_texts: Sequence[str]) -> list[RawReference]:
    """Mục danh mục trong văn bản pypdfium2: khối sau tiêu đề danh mục cuối cùng, tới tiêu
    đề phụ lục; `[]` khi không có tiêu đề danh mục. Trang của mục là trang dòng đầu.
    """
    pages = _page_lines(page_texts)
    start = _last_reference_heading(pages)
    if start is None:
        return []
    block: list[tuple[int, str]] = []
    for page, lines in enumerate(pages):
        for row, line in enumerate(lines):
            if (page, row) <= start:
                continue
            if line and _is_appendix_heading(line):
                return _split_entries(block)
            block.append((page + 1, line))
    return _split_entries(block)


async def _parse_with_grobid(grobid: GrobidClient, data: bytes, page_count: int) -> ParsedDocument:
    tei = await grobid.process_fulltext(data)
    doc = await asyncio.to_thread(parse_fulltext_tei, tei, page_count=page_count)
    if not doc.paragraphs:
        # TEI không có đoạn nào: không được ra một báo cáo "hoàn tất" rỗng.
        raise ParseError("GROBID không trích được đoạn văn nào.", code="GROBID_EMPTY")
    return doc


async def parse_pdf_document(
    data: bytes, *, grobid: GrobidClient | None, allow_fallback: bool
) -> tuple[ParsedDocument, list[dict[str, Any]]]:
    """Parse PDF của bài; trả tài liệu và cảnh báo (`SCANNED_PAGES`, `PDF_TEXT_FALLBACK`).

    Không trích được ký tự nào → `InputError` `NO_TEXT_EXTRACTED` (không gọi GROBID).
    GROBID không truy cập được → ném lại `RetryableError`, trừ khi `allow_fallback` (lần
    thử cuối hoặc không cấu hình GROBID); GROBID lỗi xử lý tài liệu → dự phòng ngay.
    """
    page_texts = await asyncio.to_thread(pdf_page_texts, data)
    scanned = await asyncio.to_thread(scanned_pages, page_texts)
    if len(scanned) == len(page_texts) and not any(map(_visible_chars, page_texts)):
        raise InputError(NO_TEXT_MESSAGE, code="NO_TEXT_EXTRACTED")
    warnings: list[dict[str, Any]] = []
    if scanned:
        warnings.append({"code": SCANNED_PAGES, "pages": scanned})
    doc: ParsedDocument | None = None
    if grobid is not None:
        try:
            doc = await _parse_with_grobid(grobid, data, len(page_texts))
        except RetryableError as exc:
            if not allow_fallback:
                raise
            logger.warning("pdf_text_fallback", extra={"error_type": type(exc).__name__})
        except ParseError as exc:
            logger.warning("pdf_text_fallback", extra={"error_type": type(exc).__name__})
    if doc is None:
        doc = await asyncio.to_thread(document_from_page_texts, page_texts)
        warnings.append({"code": PDF_TEXT_FALLBACK})
    elif doc.references_raw == []:
        # GROBID không tách được mục nào (listBibl rỗng): lấy danh mục từ văn bản pypdfium2;
        # không thấy khối danh mục thì giữ rỗng (S2 cảnh báo NO_REFERENCE_SECTION).
        entries = await asyncio.to_thread(reference_entries_from_page_texts, page_texts)
        if entries:
            doc = doc.with_references_raw(entries)
    return doc, warnings
