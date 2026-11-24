"""ParsedDocument.build (NFC, bỏ đoạn rỗng, vị trí ký tự) và đầu vào văn bản (T-205)."""

import json
import unicodedata

import pytest

from citeproof.core.enums import ChunkRegion, Language
from citeproof.core.errors import ParseError
from citeproof.parsing.docx import parse_docx
from citeproof.parsing.model import ParagraphDraft, ParsedDocument, RawReference
from citeproof.parsing.text import parse_text_payload
from tests.golden.cases import GOLDEN_DIR, load_cases


def _assert_offsets(doc: ParsedDocument) -> None:
    text = doc.text()
    for index, paragraph in enumerate(doc.paragraphs):
        assert paragraph.index == index
        assert text[paragraph.char_start : paragraph.char_end] == paragraph.text
    assert text == "\n\n".join(p.text for p in doc.paragraphs)


def test_offsets_slice_text() -> None:
    nfd = unicodedata.normalize("NFD", "Đoạn văn viết ở dạng NFD trên máy Mac.")
    drafts = [
        ParagraphDraft(text="  Chương 1. Giới thiệu ", section=None, page=1, kind="heading"),
        ParagraphDraft(text="   ", section="Chương 1. Giới thiệu", page=1),
        ParagraphDraft(text=nfd, section="Chương 1. Giới thiệu", page=1),
        ParagraphDraft(text="", section=None, page=2),
        ParagraphDraft(
            text="Bảng 1. Kết quả", section=None, page=2, region=ChunkRegion.TABLE, kind="caption"
        ),
    ]
    doc = ParsedDocument.build(source="text", drafts=drafts, page_count=2)
    assert [p.text for p in doc.paragraphs] == [
        "Chương 1. Giới thiệu",
        unicodedata.normalize("NFC", nfd),
        "Bảng 1. Kết quả",
    ]
    assert [p.page for p in doc.paragraphs] == [1, 1, 2]
    assert doc.paragraphs[2].region is ChunkRegion.TABLE
    assert doc.paragraphs[2].kind == "caption"
    assert doc.paragraphs[1].language is Language.VI
    assert doc.schema_version == "parsed/1"
    assert doc.references_raw is None
    _assert_offsets(doc)
    for case in load_cases():
        if case.input_path.suffix == ".docx":
            _assert_offsets(parse_docx(case.input_path.read_bytes()))
    _assert_offsets(parse_text_payload((GOLDEN_DIR / "vi_text_input.json").read_bytes()))


def test_build_normalises_sections_and_references() -> None:
    nfd_section = unicodedata.normalize("NFD", "Cơ sở lý thuyết")
    doc = ParsedDocument.build(
        source="text",
        drafts=[ParagraphDraft(text="Nội dung.", section=nfd_section, page=None)],
        page_count=None,
        references_raw=[
            RawReference(raw=unicodedata.normalize("NFD", " Trần Thị Bích (2021) "), label="1"),
            RawReference(raw="  "),
        ],
    )
    assert doc.paragraphs[0].section == "Cơ sở lý thuyết"
    assert doc.references_raw == [RawReference(raw="Trần Thị Bích (2021)", label="1")]


def test_parsed_document_json_roundtrip() -> None:
    doc = parse_text_payload((GOLDEN_DIR / "vi_text_input.json").read_bytes())
    assert ParsedDocument.model_validate_json(doc.model_dump_json()) == doc


def test_text_payload_references_split() -> None:
    case = next(case for case in load_cases() if case.name == "vi_text_input")
    assert case.expected is not None
    doc = parse_text_payload(case.input_path.read_bytes())
    assert doc.source == "text"
    assert doc.page_count is None
    assert doc.language is case.expected.language
    assert len(doc.paragraphs) == 2
    assert all(p.kind == "body" and p.section is None for p in doc.paragraphs)
    assert doc.references_raw is not None
    assert len(doc.references_raw) == len(case.expected.references)
    labels = [ref.label for ref in doc.references_raw]
    assert labels == [str(n) for n in range(1, len(doc.references_raw) + 1)]
    # Nhãn tách khỏi chuỗi gốc (S2 và GROBID nhận chuỗi không nhãn).
    assert doc.references_raw[0].raw.startswith("Devlin J.")
    assert all(ref.parsed is None and ref.page is None for ref in doc.references_raw)


def test_text_payload_as_stored_by_web_ui() -> None:
    # Giao diện lưu `references` là một chuỗi; trình duyệt gửi xuống dòng CRLF.
    payload = {
        "body": "Đoạn thứ nhất [1].\r\n\r\n  \r\nĐoạn thứ hai\r\nvẫn tiếp tục [2].",
        "references": "1. Nguyễn Văn An (2020). Bài báo.\r\n\r\n[2] Lê Hoàng Cường (2019).\r\n"
        "Smith J. (2010). No label.\r\n",
    }
    doc = parse_text_payload(json.dumps(payload, ensure_ascii=False).encode())
    assert [p.text for p in doc.paragraphs] == [
        "Đoạn thứ nhất [1].",
        "Đoạn thứ hai\nvẫn tiếp tục [2].",
    ]
    assert doc.references_raw == [
        RawReference(raw="Nguyễn Văn An (2020). Bài báo.", label="1"),
        RawReference(raw="Lê Hoàng Cường (2019).", label="2"),
        RawReference(raw="Smith J. (2010). No label.", label=None),
    ]


@pytest.mark.parametrize(
    "data",
    [b"not json", b"[1, 2]", b'{"body": 3, "references": ""}', b"\xff\xfe"],
)
def test_text_payload_invalid(data: bytes) -> None:
    with pytest.raises(ParseError) as info:
        parse_text_payload(data)
    assert info.value.code == "TEXT_PAYLOAD_INVALID"
    assert "not json" not in str(info.value)
