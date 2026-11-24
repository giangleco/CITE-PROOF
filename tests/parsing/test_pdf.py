"""PDF: đếm trang, trang scan, PDF mã hóa, dự phòng pypdfium2 và đường GROBID (QĐ-03, QĐ-22)."""

from collections.abc import Callable

import httpx
import pytest

from citeproof.core.errors import InputError, RetryableError
from citeproof.integrations.grobid import GrobidClient
from citeproof.parsing import pdf as pdf_module
from citeproof.parsing.model import ParsedDocument, RawReference
from citeproof.parsing.pdf import (
    document_from_page_texts,
    parse_pdf_document,
    parse_pdf_fallback,
    pdf_metadata_title,
    pdf_page_count,
    pdf_page_texts,
    reference_entries_from_page_texts,
    scanned_pages,
)
from citeproof.parsing.tei import parse_fulltext_tei
from tests.files import PDF_BYTES, stamped_pdf
from tests.golden.cases import GOLDEN_DIR, GoldenExpected

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


# --- Danh mục từ văn bản pypdfium2 khi GROBID trả `listBibl` rỗng (ruling Task 6) -----------

# Chuỗi gốc của `vi_mixed_apa.pdf` (scripts/make_golden_fixtures.py, MIXED_APA_REFS).
VI_MIXED_APA_RAWS = [
    "Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2019). BERT: Pre-training of deep "
    "bidirectional transformers for language understanding. In Proceedings of the 2019 "
    "Conference of the North American Chapter of the Association for Computational "
    "Linguistics (pp. 4171-4186). https://doi.org/10.18653/v1/N19-1423",
    "Nguyễn Văn An (2020). Phân loại văn bản tiếng Việt bằng mạng nơ-ron tích chập. "
    "Tạp chí Khoa học và Công nghệ Mẫu, 12(3), 45-58.",
    "Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., Kaiser, L., "
    "& Polosukhin, I. (2017). Attention is all you need. arXiv preprint arXiv:1706.03762. "
    "https://doi.org/10.48550/arXiv.1706.03762",
]
# Chuỗi gốc của `en_ieee.pdf` (IEEE_REFS), nhãn [n] tách riêng.
EN_IEEE_RAWS = [
    'A. Vaswani et al., "Attention is all you need," arXiv:1706.03762, 2017, '
    "doi: 10.48550/arXiv.1706.03762.",
    'J. Devlin, M.-W. Chang, K. Lee, and K. Toutanova, "BERT: Pre-training of deep '
    'bidirectional transformers for language understanding," in Proc. NAACL-HLT, 2019, '
    "pp. 4171-4186, doi: 10.18653/v1/N19-1423.",
    'K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning for image recognition," '
    "in Proc. IEEE CVPR, 2016, pp. 770-778, doi: 10.1109/CVPR.2016.90.",
    'S. Hochreiter and J. Schmidhuber, "Long short-term memory," Neural Comput., vol. 9, '
    "no. 8, pp. 1735-1780, 1997, doi: 10.1162/neco.1997.9.8.1735.",
]


def _expected(name: str) -> GoldenExpected:
    path = GOLDEN_DIR / f"{name}.expected.json"
    return GoldenExpected.model_validate_json(path.read_text(encoding="utf-8"))


class EntriesSpy:
    """Bọc `reference_entries_from_page_texts` để đếm số lần đường pypdfium2 được gọi."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, page_texts: list[str]) -> list[RawReference]:
        self.calls += 1
        return reference_entries_from_page_texts(page_texts)


async def test_empty_listbibl_takes_references_from_pdfium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = EntriesSpy()
    monkeypatch.setattr(pdf_module, "reference_entries_from_page_texts", spy)
    tei = _tei("vi_mixed_apa")  # GROBID 0.8.1 trả <listBibl/> rỗng cho tệp này
    recorder = Recorder(lambda request: httpx.Response(200, text=tei))
    doc, warnings = await parse_pdf_document(
        _pdf("vi_mixed_apa"), grobid=_grobid(recorder), allow_fallback=False
    )
    assert spy.calls == 1
    assert warnings == []
    # Thân bài, mục và trang vẫn lấy từ GROBID; chỉ danh mục lấy từ pypdfium2.
    assert doc.source == "grobid"
    assert doc.paragraphs == parse_fulltext_tei(tei, page_count=2).paragraphs
    assert doc.references_raw is not None
    assert [ref.raw for ref in doc.references_raw] == VI_MIXED_APA_RAWS
    # Chưa có trường parse: S2 xử lý như danh mục DOCX/văn bản (processCitationList, rule).
    assert all(ref.parsed is None and ref.label is None for ref in doc.references_raw)
    assert [ref.page for ref in doc.references_raw] == [2, 2, 2]
    for ref, want in zip(doc.references_raw, _expected("vi_mixed_apa").references, strict=True):
        assert want.title is not None and want.title in ref.raw
        assert str(want.year) in ref.raw
        assert want.doi is None or want.doi in ref.raw
        assert want.first_author_family is not None
        assert ref.raw.startswith(want.first_author_family)


async def test_grobid_references_skip_pdfium_reference_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = EntriesSpy()
    monkeypatch.setattr(pdf_module, "reference_entries_from_page_texts", spy)
    recorder = Recorder(lambda request: httpx.Response(200, text=_tei("en_ieee")))
    doc, _ = await parse_pdf_document(
        _pdf("en_ieee"), grobid=_grobid(recorder), allow_fallback=False
    )
    assert spy.calls == 0
    assert doc.references_raw is not None and len(doc.references_raw) == 4
    assert all(ref.parsed is not None for ref in doc.references_raw)


async def test_empty_listbibl_without_reference_heading_keeps_no_references() -> None:
    recorder = Recorder(lambda request: httpx.Response(200, text=_tei("vi_scanned_pages")))
    doc, _ = await parse_pdf_document(
        _pdf("vi_scanned_pages"), grobid=_grobid(recorder), allow_fallback=False
    )
    assert doc.source == "grobid"
    assert doc.references_raw == []  # S2 giữ cảnh báo NO_REFERENCE_SECTION


def test_reference_entries_labelled_list() -> None:
    entries = reference_entries_from_page_texts(pdf_page_texts(_pdf("en_ieee")))
    assert [ref.label for ref in entries] == ["1", "2", "3", "4"]
    assert [ref.raw for ref in entries] == EN_IEEE_RAWS
    assert {ref.page for ref in entries} == {2}
    # Dòng bắt đầu bằng số nhỏ hơn nhãn trước là dòng bị ngắt, không phải mục mới.
    numbered = reference_entries_from_page_texts(
        ["References\n[3] C. Author, vol. 12, no.\n2. pp. 1-9, 2021.\n[4] D. Author, 2022."]
    )
    assert [(ref.label, ref.raw) for ref in numbered] == [
        ("3", "C. Author, vol. 12, no. 2. pp. 1-9, 2021."),
        ("4", "D. Author, 2022."),
    ]
    zero = reference_entries_from_page_texts(["References\n[0] Z. Author, 2020.\n[1] Y. B, 2021."])
    assert [(ref.label, ref.raw) for ref in zero] == [
        ("0", "Z. Author, 2020."),
        ("1", "Y. B, 2021."),
    ]


def test_reference_entries_unlabelled_rules() -> None:
    pages = [
        "Mục lục\nTài liệu tham khảo\n1. Giới thiệu\nNội dung.",
        "Tài liệu tham khảo\n"
        "Smith, J., Brown, A.\n"  # kết thúc bằng chữ viết tắt tên: còn tiếp
        "& Lee, K. (2016).\n"  # kết thúc bằng (năm).: dòng sau là tiêu đề
        "Deep Residual Learning for Image Recognition.\n"
        "In Proceedings of CVPR (pp. 770-778). https://doi.org/\n"  # "In" không mở mục mới
        "10.1109/CVPR.2016.90\n"  # URL bị ngắt sau "/": nối liền
        "12\n"  # số trang ở chân trang
        "Nguyễn Văn An (2020). Phân loại văn bản. Tạp chí Mẫu,\n"
        "12(3), 45-58.\n"
        "Devlin, J. (2019). Some title.\n"
        "BERT: Pre-training revisited. NAACL.\n"  # "BERT:" không phải tên tác giả
        "\n"
        "bộ dữ liệu mở (2021). Không có tác giả.\n"  # sau dòng trống: mục mới
        "Phụ lục A\n"
        "Câu hỏi 1.",
    ]
    entries = reference_entries_from_page_texts(pages)
    assert [ref.raw for ref in entries] == [
        "Smith, J., Brown, A. & Lee, K. (2016). Deep Residual Learning for Image Recognition. "
        "In Proceedings of CVPR (pp. 770-778). https://doi.org/10.1109/CVPR.2016.90",
        "Nguyễn Văn An (2020). Phân loại văn bản. Tạp chí Mẫu, 12(3), 45-58.",
        "Devlin, J. (2019). Some title. BERT: Pre-training revisited. NAACL.",
        "bộ dữ liệu mở (2021). Không có tác giả.",
    ]
    assert all(ref.label is None and ref.page == 2 for ref in entries)


def test_reference_entries_without_heading() -> None:
    assert reference_entries_from_page_texts(pdf_page_texts(_pdf("vi_scanned_pages"))) == []


async def test_all_scanned_pages_pdf_has_no_text() -> None:
    data = stamped_pdf()
    texts = pdf_page_texts(data)
    assert all(text.strip() for text in texts)  # có lớp chữ, nhưng chỉ là dấu
    assert scanned_pages(texts) == [1, 2, 3]
    recorder = Recorder(lambda request: httpx.Response(500, text="[NO_BLOCKS]"))
    with pytest.raises(InputError) as caught:
        await parse_pdf_document(data, grobid=_grobid(recorder), allow_fallback=True)
    assert caught.value.code == "NO_TEXT_EXTRACTED"
    assert str(caught.value) == "Không trích được văn bản từ tệp PDF. Tệp có thể là ảnh quét."
    assert recorder.calls == 0


def test_reference_entries_reject_place_and_venue_lines() -> None:
    # Dòng nơi xuất bản và tên tạp chí sau dấu chấm không được thành mục ảo.
    page = (
        "Tài liệu tham khảo\n"
        "Trần Văn Bình (2015). Giáo trình kinh tế học.\n"
        "Hà Nội: Nhà xuất bản Giáo dục.\n"
        "Lê Văn Cường (2018). Lịch sử Việt Nam.\n"
        "TP. Hồ Chí Minh: NXB Tổng hợp.\n"
        "Hochreiter, S., & Schmidhuber, J. (1997). Long short-term memory.\n"
        "Neural Computation, 9(8), 1735-1780."
    )
    entries = reference_entries_from_page_texts([page])
    assert [ref.raw for ref in entries] == [
        "Trần Văn Bình (2015). Giáo trình kinh tế học. Hà Nội: Nhà xuất bản Giáo dục.",
        "Lê Văn Cường (2018). Lịch sử Việt Nam. TP. Hồ Chí Minh: NXB Tổng hợp.",
        "Hochreiter, S., & Schmidhuber, J. (1997). Long short-term memory. "
        "Neural Computation, 9(8), 1735-1780.",
    ]


def test_reference_entries_real_author_lines_start_entries() -> None:
    page = (
        "References\n"
        "Phạm Minh Đức (2019). Mở đầu.\n"
        "Nguyễn, V. A. (2020). Tiêu đề một.\n"
        "Lê Thị Thu Hà (2021). Tiêu đề hai.\n"
        "Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., "
        "Kaiser, L., &\n"
        "Polosukhin, I. (2017). Attention is all you need.\n"
        "Bộ Giáo dục và Đào tạo (2018). Thông tư hướng dẫn.\n"
        "OpenAI. (2023). GPT-4 technical report."
    )
    entries = reference_entries_from_page_texts([page])
    assert [ref.raw.split(" (")[0] for ref in entries] == [
        "Phạm Minh Đức",
        "Nguyễn, V. A.",
        "Lê Thị Thu Hà",
        "Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., Kaiser, L., "
        "& Polosukhin, I.",
        "Bộ Giáo dục và Đào tạo",
        "OpenAI.",
    ]
