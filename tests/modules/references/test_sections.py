"""Khối danh mục và tách mục (spec §7.2; Review Focus #2: mục ngắt dòng/ngắt đoạn được gộp).

Cùng một bộ tách mục cho S2 và đường pypdfium2 của S1 (`citeproof.parsing.references`).
"""

from collections.abc import Sequence
from xml.etree.ElementTree import fromstring

from citeproof.core.domain import ParsedReference
from citeproof.core.enums import ChunkRegion
from citeproof.modules.references.sections import (
    REFERENCE_HEADING,
    document_references,
    find_reference_block,
    reference_paragraph_indices,
    split_entries,
)
from citeproof.parsing.docx import parse_docx
from citeproof.parsing.model import ParagraphDraft, ParsedDocument, RawReference
from citeproof.parsing.pdf import (
    document_from_page_texts,
    pdf_page_texts,
    reference_entries_from_page_texts,
)
from citeproof.parsing.references import fold
from tests.golden.cases import GOLDEN_DIR

TEI = "{http://www.tei-c.org/ns/1.0}"


def _docx(name: str) -> ParsedDocument:
    return parse_docx((GOLDEN_DIR / f"{name}.docx").read_bytes())


def _recorded_raws(name: str) -> list[str]:
    """Chuỗi tham khảo đã gửi GROBID khi ghi fixture (ghi lại trong `raw_reference`)."""
    root = fromstring((GOLDEN_DIR / f"{name}.citations.tei.xml").read_text(encoding="utf-8"))
    return [
        note.text or "" for note in root.iter(f"{TEI}note") if note.get("type") == "raw_reference"
    ]


def _doc(
    paragraphs: Sequence[tuple[str, str]],
    *,
    references: Sequence[RawReference] | None = None,
    pages: Sequence[int | None] | None = None,
) -> ParsedDocument:
    """Tài liệu từ (kind, text); `pages` song song (mặc định không có số trang)."""
    page_of = list(pages) if pages is not None else [None] * len(paragraphs)
    drafts = [
        ParagraphDraft(text=text, section=None, page=page, kind=kind)  # type: ignore[arg-type]
        for (kind, text), page in zip(paragraphs, page_of, strict=True)
    ]
    return ParsedDocument.build(
        source="docx", drafts=drafts, page_count=None, references_raw=references
    )


def test_reference_heading_regex_matches_folded_variants() -> None:
    for heading in (
        "Tài liệu tham khảo",
        "DANH MỤC TÀI LIỆU THAM KHẢO",
        "References",
        "Bibliography",
        "Works Cited",
    ):
        assert REFERENCE_HEADING.fullmatch(fold(heading)) is not None, heading
    assert REFERENCE_HEADING.fullmatch(fold("Tài liệu")) is None


def test_find_block_after_last_heading_until_appendix() -> None:
    doc = _doc(
        [
            ("heading", "Mục lục"),
            ("body", "Tài liệu tham khảo"),  # dòng mục lục: không phải khối cuối
            ("body", "1. Giới thiệu"),
            ("heading", "Chương 1"),
            ("body", "Nội dung có trích dẫn [1]."),
            ("body", "TÀI LIỆU THAM KHẢO"),  # tiêu đề in đậm, không dùng style Heading
            ("body", "[1] Nguyễn Văn An (2020), Bài báo."),
            ("body", "[2] Lê Hoàng Cường (2019), Giáo trình."),
            ("heading", "Phụ lục A"),
            ("body", "[3] Không phải tài liệu tham khảo."),
        ]
    )
    block = find_reference_block(doc)
    assert [p.index for p in block] == [6, 7]
    assert reference_paragraph_indices(doc) == frozenset({5, 6, 7})


def test_no_heading_gives_empty_block() -> None:
    doc = _doc([("body", "Không có danh mục."), ("body", "References are discussed here.")])
    assert find_reference_block(doc) == []
    assert reference_paragraph_indices(doc) == frozenset()
    assert document_references(doc) == []


def test_heading_inside_table_is_ignored() -> None:
    doc = ParsedDocument.build(
        source="docx",
        drafts=[
            ParagraphDraft(text="Tài liệu tham khảo", section=None, page=None),
            ParagraphDraft(text="Nguyễn Văn An (2020). Một.", section=None, page=None),
            ParagraphDraft(text="References", section=None, page=None, region=ChunkRegion.TABLE),
            ParagraphDraft(text="Lê Hoàng Cường (2019). Hai.", section=None, page=None),
        ],
        page_count=None,
    )
    assert [p.index for p in find_reference_block(doc)] == [1, 2, 3]


def test_multiline_docx_entries_merged_with_numbering_intact() -> None:
    doc = _docx("multiline_refs")
    entries = split_entries(find_reference_block(doc))
    assert [ref.label for ref in entries] == ["1", "2", "3", "4"]
    assert entries[1].raw == (
        "Trần Thị Bích (2021), Nhận dạng thực thể có tên trong văn bản "
        "tiếng Việt, Luận văn thạc sĩ, Trường Đại học Mẫu, Hà Nội."
    )
    # Đúng chuỗi đã gửi GROBID khi ghi fixture: các dòng nối bằng một khoảng trắng.
    assert [ref.raw for ref in entries] == _recorded_raws("multiline_refs")
    assert reference_paragraph_indices(doc) == frozenset(range(1, 12))


def test_docx_one_paragraph_per_entry() -> None:
    entries = document_references(_docx("en_paper_author_year"))
    assert [ref.raw for ref in entries] == _recorded_raws("en_paper_author_year")
    assert all(ref.label is None and ref.parsed is None for ref in entries)
    numeric = document_references(_docx("vi_thesis_numeric"))
    assert [ref.label for ref in numeric] == ["1", "2", "3", "4"]
    assert [ref.raw for ref in numeric] == _recorded_raws("vi_thesis_numeric")


def test_soft_line_breaks_inside_docx_paragraph_are_joined() -> None:
    # Hanging indent gõ bằng Shift+Enter: một đoạn, nhiều dòng.
    doc = _doc(
        [
            ("heading", "References"),
            (
                "body",
                "Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2019). BERT: Pre-training\n"
                "of deep bidirectional transformers. In Proceedings of NAACL\n"
                "(pp. 4171-4186).",
            ),
            ("body", "He, K., Zhang, X. (2016). Deep residual learning."),
        ]
    )
    entries = document_references(doc)
    assert [ref.raw for ref in entries] == [
        "Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K. (2019). BERT: Pre-training "
        "of deep bidirectional transformers. In Proceedings of NAACL (pp. 4171-4186).",
        "He, K., Zhang, X. (2016). Deep residual learning.",
    ]


def test_pdfium_documents_use_the_same_splitter_as_s1() -> None:
    for name, count in (("en_ieee", 4), ("vi_mixed_apa", 3)):
        texts = pdf_page_texts((GOLDEN_DIR / f"{name}.pdf").read_bytes())
        entries = document_references(document_from_page_texts(texts))
        assert len(entries) == count, name
        assert entries == reference_entries_from_page_texts(texts), name


def test_unlabelled_entry_continues_across_pdf_pages() -> None:
    doc = document_from_page_texts(
        [
            "1. Giới thiệu\nNội dung.\nReferences\n"
            "Devlin, J. (2019). BERT: Pre-training of deep bidirectional",
            "transformers for language understanding. NAACL.\n"
            "He, K. (2016). Deep residual learning. CVPR.",
        ]
    )
    assert [ref.raw for ref in document_references(doc)] == [
        "Devlin, J. (2019). BERT: Pre-training of deep bidirectional transformers for "
        "language understanding. NAACL.",
        "He, K. (2016). Deep residual learning. CVPR.",
    ]


def test_language_group_subheadings_are_dropped_and_reset_numbering() -> None:
    doc = _doc(
        [
            ("heading", "Tài liệu tham khảo"),
            ("body", "Tiếng Việt"),
            ("body", "[1] Nguyễn Văn An (2020), Bài báo một."),
            ("body", "[2] Lê Hoàng Cường (2019), Giáo trình."),
            ("heading", "Tài liệu tiếng Anh"),
            ("body", "[1] Vaswani A. et al. (2017), Attention is all you need,"),
            ("body", "arXiv preprint arXiv:1706.03762."),
        ]
    )
    entries = document_references(doc)
    assert [(ref.label, ref.raw) for ref in entries] == [
        ("1", "Nguyễn Văn An (2020), Bài báo một."),
        ("2", "Lê Hoàng Cường (2019), Giáo trình."),
        ("1", "Vaswani A. et al. (2017), Attention is all you need, arXiv preprint "
         "arXiv:1706.03762."),
    ]  # fmt: skip


def test_wrapped_line_starting_with_number_stays_in_entry() -> None:
    doc = _doc(
        [
            ("heading", "References"),
            ("body", "[3] C. Author, vol. 12, no."),
            ("body", "2. pp. 1-9, 2021."),
            ("body", "[4] D. Author, 2022."),
        ]
    )
    assert [(ref.label, ref.raw) for ref in document_references(doc)] == [
        ("3", "C. Author, vol. 12, no. 2. pp. 1-9, 2021."),
        ("4", "D. Author, 2022."),
    ]


def test_mostly_unlabelled_list_ignores_a_stray_number() -> None:
    doc = _doc(
        [
            ("heading", "References"),
            ("body", "Devlin, J. (2019). BERT. NAACL."),
            ("body", "He, K. (2016). Deep residual learning. CVPR."),
            ("body", "1. Hochreiter, S. (1997). Long short-term memory."),
        ]
    )
    entries = document_references(doc)
    assert [ref.label for ref in entries] == [None, None, None]
    assert entries[2].raw == "1. Hochreiter, S. (1997). Long short-term memory."


def test_text_references_join_unlabelled_continuations_when_mostly_labelled() -> None:
    refs = [
        RawReference(raw="Nguyễn Văn An (2020), Bài báo", label="1"),
        RawReference(raw="tiếp theo dòng.", label=None),
        RawReference(raw="Lê Hoàng Cường (2019).", label="2"),
    ]
    doc = _doc([("body", "Thân bài [1].")], references=refs)
    assert document_references(doc) == [
        RawReference(raw="Nguyễn Văn An (2020), Bài báo tiếp theo dòng.", label="1"),
        RawReference(raw="Lê Hoàng Cường (2019).", label="2"),
    ]


def test_grobid_references_are_used_as_is() -> None:
    refs = [
        RawReference(raw="A. One, “T1,” 2019.", page=2, parsed=ParsedReference(title="T1")),
        RawReference(raw="B. Two, “T2,” 2020.", page=2, parsed=ParsedReference(title="T2")),
    ]
    doc = _doc([("body", "Thân bài [1].")], references=refs)
    assert document_references(doc) == refs


def test_empty_references_raw_falls_back_to_block() -> None:
    doc = _doc(
        [("heading", "References"), ("body", "[1] Nguyễn Văn An (2020), Bài báo.")],
        references=[],
    )
    assert document_references(doc) == [
        RawReference(raw="Nguyễn Văn An (2020), Bài báo.", label="1")
    ]


def test_heading_styled_subheading_resets_numbering_and_is_dropped() -> None:
    doc = _doc(
        [
            ("heading", "Tài liệu tham khảo"),
            ("body", "[1] Nguyễn Văn An (2020), Bài báo một."),
            ("body", "[2] Lê Hoàng Cường (2019), Giáo trình."),
            ("heading", "Sách và giáo trình khác"),  # không khớp mẫu tiêu đề nhóm
            ("body", "1. Trần Thị Bích (2021), Sách."),
            ("body", "2. Phạm Minh Đức (2018), Sách hai."),
        ]
    )
    assert [(ref.label, ref.raw) for ref in document_references(doc)] == [
        ("1", "Nguyễn Văn An (2020), Bài báo một."),
        ("2", "Lê Hoàng Cường (2019), Giáo trình."),
        ("1", "Trần Thị Bích (2021), Sách."),
        ("2", "Phạm Minh Đức (2018), Sách hai."),
    ]


def test_restart_after_unrecognised_text_subheading_in_docx() -> None:
    doc = _doc(
        [
            ("heading", "Tài liệu tham khảo"),
            ("body", "[1] Nguyễn A (2020). X."),
            ("body", "[2] Lê B (2019). Y."),
            ("body", "Tài liệu tiếng nước ngoài"),
            ("body", "[1] Smith, C. (2017). Z."),
            ("body", "[2] Doe, D. (2018). W."),
        ]
    )
    entries = document_references(doc)
    assert [ref.label for ref in entries] == ["1", "2", "1", "2"]
    assert entries[1].raw == "Lê B (2019). Y."


def test_label_only_paragraphs_give_clean_entries() -> None:
    doc = _doc(
        [
            ("heading", "References"),
            ("body", "[1]"),
            ("body", "A. Author, “Title one,” 2020."),
            ("body", "[2] B. Author, “Title two,” 2021."),
            ("body", "[3]"),
        ]
    )
    assert [(ref.label, ref.raw) for ref in document_references(doc)] == [
        ("1", "A. Author, “Title one,” 2020."),
        ("2", "B. Author, “Title two,” 2021."),
    ]


def test_label_only_stored_references_are_dropped() -> None:
    refs = [
        RawReference(raw="[3]", label=None),
        RawReference(raw="A. One, “T1,” 2019.", page=2, parsed=ParsedReference(title="T1")),
    ]
    doc = _doc([("body", "Thân bài [1].")], references=refs)
    assert document_references(doc) == refs[1:]
