"""Parse DOCX (T-204): đoạn, tiêu đề mục, bảng, chú thích; từ chối macro và zip bomb."""

import io
import zipfile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from citeproof.core.enums import ChunkRegion
from citeproof.core.errors import InputError
from citeproof.parsing.docx import parse_docx
from citeproof.parsing.encoding import detect_legacy_encoding
from tests.golden.cases import GOLDEN_DIR, GoldenCase

EXPECTED_HEADINGS = {
    "vi_thesis_numeric": [
        "Chương 1. Giới thiệu",
        "Chương 2. Cơ sở lý thuyết",
        "Tài liệu tham khảo",
    ],
    "en_paper_author_year": [
        "A Short Survey of Sequence Models",
        "1. Introduction",
        "2. Related Work",
        "References",
    ],
    "vi_tcvn3_legacy": [],
    "multiline_refs": ["Tài liệu tham khảo"],
}


def test_docx_golden(golden_docx_case: GoldenCase) -> None:
    case = golden_docx_case
    assert case.expected is not None
    doc = parse_docx(case.input_path.read_bytes())
    assert doc.source == "docx"
    assert doc.page_count is None
    assert doc.references_raw is None
    assert all(p.page is None for p in doc.paragraphs)
    assert doc.language is case.expected.language
    # Tiêu đề thành mục: mỗi đoạn thuộc tiêu đề gần nhất phía trước (preflight F20).
    assert [p.text for p in doc.paragraphs if p.kind == "heading"] == EXPECTED_HEADINGS[case.name]
    section: str | None = None
    for paragraph in doc.paragraphs:
        if paragraph.kind == "heading":
            section = paragraph.text
        assert paragraph.section == section
    # parse_docx không trả cảnh báo; bảng mã cũ đọc qua detect_legacy_encoding (F20).
    legacy = detect_legacy_encoding(doc.text()) is not None
    assert legacy == ("LEGACY_ENCODING" in case.expected.warnings)


def _save(document: object) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)  # type: ignore[attr-defined]
    return buffer.getvalue()


def test_docx_outline_levels_tables_and_captions() -> None:
    document = Document()
    chapter = document.styles.add_style("Chuong", WD_STYLE_TYPE.PARAGRAPH)
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "0")
    chapter.element.get_or_add_pPr().append(outline)
    document.add_paragraph("Mở đầu không thuộc mục nào.")
    document.add_paragraph("Chương 1. Tổng quan", style="Chuong")
    direct = document.add_paragraph("1.1. Bối cảnh")
    direct_outline = OxmlElement("w:outlineLvl")
    direct_outline.set(qn("w:val"), "1")
    direct._p.get_or_add_pPr().append(direct_outline)
    body = document.add_paragraph("Đoạn thân bài với ký hiệu [1].")
    body_outline = OxmlElement("w:outlineLvl")
    body_outline.set(qn("w:val"), "9")  # mức 9 là thân bài
    body._p.get_or_add_pPr().append(body_outline)
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Mô hình"
    table.cell(0, 1).text = "Độ chính xác [2]"
    document.add_paragraph("Bảng 1.1. Kết quả thực nghiệm", style="Caption")
    document.add_paragraph("Hình 1.2. Kiến trúc hệ thống", style="Caption")
    document.add_heading("Tài liệu tham khảo", level=1)
    document.add_paragraph("[1] Nguyễn Văn An (2020).")

    doc = parse_docx(_save(document))
    rows = [(p.text, p.kind, p.section, p.region) for p in doc.paragraphs]
    body_region = ChunkRegion.BODY
    assert rows == [
        ("Mở đầu không thuộc mục nào.", "body", None, body_region),
        ("Chương 1. Tổng quan", "heading", "Chương 1. Tổng quan", body_region),
        ("1.1. Bối cảnh", "heading", "1.1. Bối cảnh", body_region),
        ("Đoạn thân bài với ký hiệu [1].", "body", "1.1. Bối cảnh", body_region),
        ("Mô hình", "body", "1.1. Bối cảnh", ChunkRegion.TABLE),
        ("Độ chính xác [2]", "body", "1.1. Bối cảnh", ChunkRegion.TABLE),
        ("Bảng 1.1. Kết quả thực nghiệm", "caption", "1.1. Bối cảnh", ChunkRegion.TABLE),
        ("Hình 1.2. Kiến trúc hệ thống", "caption", "1.1. Bối cảnh", ChunkRegion.FIGURE),
        ("Tài liệu tham khảo", "heading", "Tài liệu tham khảo", body_region),
        ("[1] Nguyễn Văn An (2020).", "body", "Tài liệu tham khảo", body_region),
    ]


def _with_extra_entry(name: str, data: bytes) -> bytes:
    source = (GOLDEN_DIR / "vi_thesis_numeric.docx").read_bytes()
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr(name, data)
    return buffer.getvalue()


def test_docx_with_vba_rejected() -> None:
    with pytest.raises(InputError) as info:
        parse_docx(_with_extra_entry("word/vbaProject.bin", b"\xd0\xcf\x11\xe0macro"))
    assert info.value.code == "DOCX_MACRO_NOT_ALLOWED"


def test_docx_zip_bomb_rejected() -> None:
    # 5 MB số 0 nén còn vài KB; tổng `file_size` khai báo vượt giới hạn: từ chối trước khi
    # giải nén.
    data = _with_extra_entry("word/media/padding.bin", bytes(5_000_000))
    assert len(data) < 100_000
    with pytest.raises(InputError) as info:
        parse_docx(data, max_uncompressed_bytes=4_000_000)
    assert info.value.code == "FILE_TOO_LARGE"
    # Cùng tệp, giới hạn mặc định 200 MB: đọc bình thường.
    assert parse_docx(data).paragraphs


@pytest.mark.parametrize("data", [b"PK\x03\x04hong", b"%PDF-1.7", b""])
def test_docx_unreadable_rejected(data: bytes) -> None:
    with pytest.raises(InputError) as info:
        parse_docx(data)
    assert info.value.code == "UNSUPPORTED_FILE_TYPE"


def test_docx_without_text_rejected() -> None:
    document = Document()
    document.add_paragraph("   ")
    document.add_table(rows=1, cols=1)
    with pytest.raises(InputError) as info:
        parse_docx(_save(document))
    assert info.value.code == "NO_TEXT_EXTRACTED"


def test_docx_corrupt_part_rejected() -> None:
    # Dòng nén của word/document.xml bị hỏng: zipfile ném zlib.error hoặc BadZipFile.
    source = (GOLDEN_DIR / "vi_thesis_numeric.docx").read_bytes()
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        info = archive.getinfo("word/document.xml")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    damaged = bytearray(source)
    for offset in range(start + 10, start + 60):
        damaged[offset] ^= 0xFF
    with pytest.raises(InputError) as raised:
        parse_docx(bytes(damaged))
    assert raised.value.code == "UNSUPPORTED_FILE_TYPE"
