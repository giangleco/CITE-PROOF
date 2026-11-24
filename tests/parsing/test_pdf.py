"""PDF: đếm trang, trang scan, PDF mã hóa, dự phòng pypdfium2 và đường GROBID (QĐ-03, QĐ-22)."""

from collections.abc import Callable

import httpx
import pytest

from citeproof.core.errors import InputError, RetryableError
from citeproof.integrations.grobid import GrobidClient
from citeproof.parsing.model import ParsedDocument
from citeproof.parsing.pdf import (
    document_from_page_texts,
    parse_pdf_document,
    parse_pdf_fallback,
    pdf_metadata_title,
    pdf_page_count,
    pdf_page_texts,
    scanned_pages,
)
from tests.files import PDF_BYTES
from tests.golden.cases import GOLDEN_DIR

Handler = Callable[[httpx.Request], httpx.Response]


def _pdf(name: str) -> bytes:
    return (GOLDEN_DIR / f"{name}.pdf").read_bytes()


def _tei(name: str) -> str:
    return (GOLDEN_DIR / f"{name}.fulltext.tei.xml").read_text(encoding="utf-8")


class Recorder:
    """Handler MockTransport đếm số lời gọi rồi trả phản hồi (hoặc ném lỗi) cố định."""

    def __init__(self, respond: Handler) -> None:
        self.respond = respond
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return self.respond(request)


def _grobid(handler: Handler) -> GrobidClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GrobidClient("http://grobid.test:8070", http, timeout_s=5.0)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


def _assert_offsets(doc: ParsedDocument) -> None:
    text = doc.text()
    for paragraph in doc.paragraphs:
        assert text[paragraph.char_start : paragraph.char_end] == paragraph.text


def test_page_count_and_texts() -> None:
    assert pdf_page_count(_pdf("en_ieee")) == 2
    assert pdf_page_count(_pdf("vi_scanned_pages")) == 3
    texts = pdf_page_texts(_pdf("vi_scanned_pages"))
    assert len(texts) == 3
    assert texts[0].startswith("Báo cáo tiến độ thực hiện đề tài\nTrang này")
    assert "\r" not in texts[0]
    assert texts[1] == ""
    assert pdf_page_texts(_pdf("vi_scanned_pages"), max_pages=1) == texts[:1]


def test_scanned_pages() -> None:
    assert scanned_pages(pdf_page_texts(_pdf("vi_scanned_pages"))) == [2]
    assert scanned_pages(pdf_page_texts(_pdf("image_only"))) == [1, 2]
    # Ngưỡng tính trên ký tự không phải khoảng trắng: số trang ở chân trang vẫn là trang scan.
    assert scanned_pages(["  12  \n", "x" * 24, "y" * 25, " a " * 30], min_chars=25) == [1, 2]


@pytest.mark.parametrize(
    "read",
    [pdf_page_count, pdf_page_texts, parse_pdf_fallback, pdf_metadata_title],
)
def test_encrypted_pdf_rejected(read: Callable[[bytes], object]) -> None:
    with pytest.raises(InputError) as caught:
        read(_pdf("encrypted"))
    assert caught.value.code == "PDF_ENCRYPTED"
    assert str(caught.value).startswith("Tệp PDF được bảo vệ bằng mật khẩu")


def test_corrupt_pdf_rejected() -> None:
    with pytest.raises(InputError) as caught:
        pdf_page_count(PDF_BYTES)
    assert caught.value.code == "UNSUPPORTED_FILE_TYPE"
    assert str(caught.value).startswith("Không đọc được tệp PDF")


def test_metadata_title() -> None:
    assert pdf_metadata_title(_pdf("en_ieee")) == "en_ieee"


def test_fallback_en_ieee() -> None:
    doc = parse_pdf_fallback(_pdf("en_ieee"))
    assert doc.source == "pdfium"
    assert doc.page_count == 2
    assert doc.references_raw is None  # S2 tìm khối danh mục theo tiêu đề
    _assert_offsets(doc)
    headings = [(p.text, p.page) for p in doc.paragraphs if p.kind == "heading"]
    assert headings == [
        ("I. Introduction", 1),
        ("II. Method", 1),
        ("III. Results", 2),
        ("References", 2),
    ]
    assert doc.paragraphs[0].section is None
    assert doc.paragraphs[0].text.startswith("Sequence Models for Citation Checking\n")
    refs = doc.paragraphs[-1]
    assert refs.section == "References"
    assert refs.kind == "body"
    # Giữ xuống dòng của trang để S2 tách mục theo nhãn đầu dòng.
    assert refs.text.startswith('[1] A. Vaswani et al., "Attention is all you need,"')
    assert "\n[2] J. Devlin" in refs.text


def test_fallback_vi_mixed_apa() -> None:
    doc = parse_pdf_fallback(_pdf("vi_mixed_apa"))
    headings = [(p.text, p.page) for p in doc.paragraphs if p.kind == "heading"]
    assert headings == [("1. Giới thiệu", 1), ("2. Cơ sở lý thuyết", 1), ("Tài liệu tham khảo", 2)]
    assert doc.paragraphs[-1].section == "Tài liệu tham khảo"
    assert doc.paragraphs[-1].text.startswith("Devlin, J., Chang, M.-W.")


def test_fallback_heading_rules() -> None:
    pages = [
        "1. Giới thiệu\r\nNội dung mở đầu.\r\n12 tháng sau, kết quả được công bố\r\n"
        "2019 Conference of the North American Chapter\r\n\r\nĐoạn mới sau dòng trống.\r\n"
        "1.1 Mục tiêu\r\nCâu một;\r\n2. Thu thập dữ liệu;\r\nIV. Kết luận\r\nHết.",
        "",
        "TÀI LIỆU THAM KHẢO\n1. Nguyễn Văn An (2020). Phân loại văn bản tiếng Việt bằng mạng\n"
        "2. Trần Thị Bích (2021). Nhận dạng thực thể có tên trong văn bản\n"
        "Phụ lục A. Bộ câu hỏi\nCâu hỏi 1.",
    ]
    doc = document_from_page_texts(pages)
    assert doc.page_count == 3
    rows = [(p.kind, p.section, p.page, p.text) for p in doc.paragraphs]
    assert rows == [
        ("heading", "1. Giới thiệu", 1, "1. Giới thiệu"),
        (
            "body",
            "1. Giới thiệu",
            1,
            "Nội dung mở đầu.\n12 tháng sau, kết quả được công bố\n"
            "2019 Conference of the North American Chapter",
        ),
        ("body", "1. Giới thiệu", 1, "Đoạn mới sau dòng trống."),
        ("heading", "1.1 Mục tiêu", 1, "1.1 Mục tiêu"),
        ("body", "1.1 Mục tiêu", 1, "Câu một;\n2. Thu thập dữ liệu;"),
        ("heading", "IV. Kết luận", 1, "IV. Kết luận"),
        ("body", "IV. Kết luận", 1, "Hết."),
        ("heading", "TÀI LIỆU THAM KHẢO", 3, "TÀI LIỆU THAM KHẢO"),
        (
            "body",
            "TÀI LIỆU THAM KHẢO",
            3,
            "1. Nguyễn Văn An (2020). Phân loại văn bản tiếng Việt bằng mạng\n"
            "2. Trần Thị Bích (2021). Nhận dạng thực thể có tên trong văn bản",
        ),
        ("heading", "Phụ lục A. Bộ câu hỏi", 3, "Phụ lục A. Bộ câu hỏi"),
        ("body", "Phụ lục A. Bộ câu hỏi", 3, "Câu hỏi 1."),
    ]
    _assert_offsets(doc)


async def test_parse_pdf_document_uses_grobid() -> None:
    recorder = Recorder(lambda request: httpx.Response(200, text=_tei("en_ieee")))
    doc, warnings = await parse_pdf_document(
        _pdf("en_ieee"), grobid=_grobid(recorder), allow_fallback=False
    )
    assert recorder.calls == 1
    assert doc.source == "grobid"
    assert doc.page_count == 2
    assert doc.references_raw is not None and len(doc.references_raw) == 4
    assert warnings == []


async def test_parse_pdf_document_scanned_pages_warning() -> None:
    recorder = Recorder(lambda request: httpx.Response(200, text=_tei("vi_scanned_pages")))
    doc, warnings = await parse_pdf_document(
        _pdf("vi_scanned_pages"), grobid=_grobid(recorder), allow_fallback=False
    )
    assert doc.source == "grobid"
    assert doc.page_count == 3
    assert warnings == [{"code": "SCANNED_PAGES", "pages": [2]}]


async def test_parse_pdf_document_without_grobid_falls_back() -> None:
    doc, warnings = await parse_pdf_document(_pdf("en_ieee"), grobid=None, allow_fallback=True)
    assert doc.source == "pdfium"
    assert warnings == [{"code": "PDF_TEXT_FALLBACK"}]


async def test_parse_pdf_document_unavailable_grobid() -> None:
    recorder = Recorder(_refuse)
    with pytest.raises(RetryableError):
        await parse_pdf_document(_pdf("en_ieee"), grobid=_grobid(recorder), allow_fallback=False)
    doc, warnings = await parse_pdf_document(
        _pdf("en_ieee"), grobid=_grobid(recorder), allow_fallback=True
    )
    assert recorder.calls == 2
    assert doc.source == "pdfium"
    assert warnings == [{"code": "PDF_TEXT_FALLBACK"}]


@pytest.mark.parametrize(
    "respond",
    [
        lambda request: httpx.Response(500, text="[BAD_INPUT_DATA]"),
        lambda request: httpx.Response(200, text="<TEI><text>"),
        # TEI hợp lệ nhưng không có đoạn nào: không được ra một báo cáo rỗng.
        lambda request: httpx.Response(
            200, text='<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body/></text></TEI>'
        ),
    ],
    ids=["http-500", "malformed-tei", "empty-tei"],
)
async def test_parse_pdf_document_grobid_failure_falls_back_immediately(respond: Handler) -> None:
    doc, warnings = await parse_pdf_document(
        _pdf("vi_scanned_pages"), grobid=_grobid(Recorder(respond)), allow_fallback=False
    )
    assert doc.source == "pdfium"
    assert doc.page_count == 3
    assert [p.page for p in doc.paragraphs] == [1, 3]
    assert warnings == [{"code": "SCANNED_PAGES", "pages": [2]}, {"code": "PDF_TEXT_FALLBACK"}]


async def test_image_only_pdf_has_no_text() -> None:
    recorder = Recorder(lambda request: httpx.Response(500, text="[NO_BLOCKS]"))
    with pytest.raises(InputError) as caught:
        await parse_pdf_document(_pdf("image_only"), grobid=_grobid(recorder), allow_fallback=True)
    assert caught.value.code == "NO_TEXT_EXTRACTED"
    assert str(caught.value) == "Không trích được văn bản từ tệp PDF. Tệp có thể là ảnh quét."
    assert recorder.calls == 0  # không gửi tệp không có chữ cho GROBID
