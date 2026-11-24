"""S6 — chia đoạn nguồn (spec §7.6, `07a` §4.8.4): theo mục, 250-400 token, chồng lấn một câu."""

import itertools
import random
import unicodedata
from collections import defaultdict
from collections.abc import Sequence

from citeproof.core.enums import ChunkRegion
from citeproof.modules.sources.chunking import (
    ChunkDraft,
    chunk_document,
    estimate_tokens,
    sentence_spans,
)
from citeproof.parsing.model import ParagraphDraft, ParagraphKind, ParsedDocument

TABLE_CAPTION = "Table 1: Accuracy of the recurrent baseline and the fine-tuned encoder."
FIGURE_CAPTION = "Figure 2: Learning curves of both models on the development set."


def _sentence(k: int, words: int = 18) -> str:
    """Câu tiếng Anh thứ `k`, đúng `words` từ, kết thúc bằng dấu chấm (không trùng câu khác)."""
    return " ".join(
        [f"Sentence{k}", "shows", "that", "the", *(["token"] * (words - 6)), "of", "end."]
    )


SENTENCES = [_sentence(k) for k in range(400)]


def _paragraph(first: int, count: int) -> str:
    return " ".join(SENTENCES[first : first + count])


def _draft(
    text: str,
    section: str | None,
    *,
    page: int | None = 1,
    kind: ParagraphKind = "body",
    region: ChunkRegion = ChunkRegion.BODY,
) -> ParagraphDraft:
    return ParagraphDraft(text=text, section=section, page=page, kind=kind, region=region)


def _heading(title: str, *, page: int = 1) -> ParagraphDraft:
    return _draft(title, title, page=page, kind="heading")


ABSTRACT = _paragraph(0, 6)


def _document() -> ParsedDocument:
    """Abstract, phần mở đầu nhiều đoạn ngắn, một đoạn rất dài, phần kết quả có bảng và hình."""
    drafts = [
        _draft(ABSTRACT, "Abstract"),
        _heading("1 Introduction"),
        *(_draft(_paragraph(10 + 5 * i, 5), "1 Introduction") for i in range(8)),
        _heading("2 Method", page=2),
        _draft(_paragraph(60, 40), "2 Method", page=2),  # ~936 token: một đoạn > 400
        _heading("3 Results", page=3),
        _draft(_paragraph(120, 6), "3 Results", page=3),
        _draft(_paragraph(126, 6), "3 Results", page=3),
        _draft(TABLE_CAPTION, "3 Results", page=3, kind="caption", region=ChunkRegion.TABLE),
        _draft(FIGURE_CAPTION, "3 Results", page=4, kind="caption", region=ChunkRegion.FIGURE),
        _heading("4 Discussion", page=4),
        _heading("4.1 Limitations", page=4),
        _draft(_paragraph(140, 3), "4.1 Limitations", page=4),
    ]
    return ParsedDocument.build(source="grobid", drafts=drafts, page_count=4)


def _body_by_section(chunks: Sequence[ChunkDraft]) -> dict[str | None, list[ChunkDraft]]:
    grouped: dict[str | None, list[ChunkDraft]] = defaultdict(list)
    for chunk in chunks:
        if chunk.region is ChunkRegion.BODY:
            grouped[chunk.section].append(chunk)
    return grouped


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("một hai ba") == 4  # ceil(3 * 1,3)
    assert estimate_tokens("  a\n\nb\tc  ") == 4  # mọi loại khoảng trắng tách từ


def test_sentence_spans_keep_abbreviations_and_decimals() -> None:
    text = "Kết quả đạt 3.5 điểm (Nguyễn et al. 2020). Xem Fig. 2 và e.g. bảng. Đ. Minh viết. Hết"
    assert [text[a:b] for a, b in sentence_spans(text)] == [
        "Kết quả đạt 3.5 điểm (Nguyễn et al. 2020).",
        "Xem Fig. 2 và e.g. bảng.",
        "Đ. Minh viết.",
        "Hết",
    ]


def test_offsets_slice_parsed_text() -> None:
    doc = _document()
    text = doc.text()
    chunks = chunk_document(doc)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert [c.char_start for c in chunks] == sorted(c.char_start for c in chunks)
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.text == chunk.text.strip()
        assert chunk.language == "en"
    method = [c for c in chunks if c.section == "2 Method"]
    assert {c.page for c in method} == {2}


def test_chunks_respect_section_boundaries() -> None:
    doc = _document()
    chunks = chunk_document(doc)
    for chunk in chunks:
        covered = [
            p
            for p in doc.paragraphs
            if p.char_start < chunk.char_end and chunk.char_start < p.char_end
        ]
        assert covered
        assert {p.section for p in covered} == {chunk.section}
    # Mục chỉ có tiêu đề ("4 Discussion") không sinh chunk; mọi mục có nội dung đều có.
    assert [s for s in dict.fromkeys(c.section for c in chunks)] == [
        "Abstract",
        "1 Introduction",
        "2 Method",
        "3 Results",
        "4.1 Limitations",
    ]


def test_chunk_token_window() -> None:
    chunks = chunk_document(_document())
    grouped = _body_by_section(chunks)
    assert len(grouped["1 Introduction"]) >= 3
    assert len(grouped["2 Method"]) >= 3  # đoạn dài hơn 400 token bị tách theo câu
    for section_chunks in grouped.values():
        for chunk in section_chunks:
            assert estimate_tokens(chunk.text) <= 400
        for chunk in section_chunks[:-1]:
            assert estimate_tokens(chunk.text) >= 250


def test_one_sentence_overlap() -> None:
    chunks = chunk_document(_document())
    pairs = 0
    for section_chunks in _body_by_section(chunks).values():
        for current, following in itertools.pairwise(section_chunks):
            [last] = [s for s in SENTENCES if current.text.endswith(s)]
            assert following.text.startswith(last)
            # Chồng lấn đúng một câu: chunk sau bắt đầu ngay ở câu cuối của chunk trước.
            assert following.char_start == current.char_end - len(last)
            pairs += 1
    assert pairs >= 4


def test_captions_separate_with_region() -> None:
    chunks = chunk_document(_document())
    [table] = [c for c in chunks if c.region is ChunkRegion.TABLE]
    [figure] = [c for c in chunks if c.region is ChunkRegion.FIGURE]
    assert (table.text, table.section, table.page) == (TABLE_CAPTION, "3 Results", 3)
    assert (figure.text, figure.section, figure.page) == (FIGURE_CAPTION, "3 Results", 4)
    for chunk in chunks:
        if chunk.region is ChunkRegion.BODY:
            assert "Table 1" not in chunk.text and "Figure 2" not in chunk.text


def test_abstract_is_own_chunk() -> None:
    chunks = chunk_document(_document())
    [abstract] = [c for c in chunks if c.section == "Abstract"]
    assert abstract.text == ABSTRACT
    assert abstract.region is ChunkRegion.BODY
    assert abstract.chunk_index == 0
    assert all(SENTENCES[0] not in c.text for c in chunks[1:])


def test_heading_starts_first_chunk_of_section() -> None:
    chunks = chunk_document(_document())
    [first_method] = [c for c in chunks if c.section == "2 Method"][:1]
    assert first_method.text.startswith("2 Method\n\nSentence60 ")


def test_overlong_sentence_split_at_words() -> None:
    words = [f"w{i}" for i in range(1000)]
    doc = ParsedDocument.build(
        source="pdfium", drafts=[_draft(" ".join(words), "Data")], page_count=1
    )
    chunks = chunk_document(doc)
    assert len(chunks) >= 3
    text = doc.text()
    for chunk in chunks:
        assert estimate_tokens(chunk.text) <= 400
        assert text[chunk.char_start : chunk.char_end] == chunk.text
    seen = [w for chunk in chunks for w in chunk.text.split()]
    assert list(dict.fromkeys(seen)) == words  # đủ mọi từ, theo thứ tự


def test_chunk_text_is_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Mô hình đạt độ chính xác cao trên tập kiểm tra tiếng Việt.")
    doc = ParsedDocument.build(source="text", drafts=[_draft(nfd, None)], page_count=None)
    [chunk] = chunk_document(doc)
    assert chunk.text == unicodedata.normalize("NFC", nfd)
    assert unicodedata.is_normalized("NFC", chunk.text)
    assert chunk.language == "vi"
    assert chunk.section is None and chunk.page == 1


def test_empty_document_has_no_chunks() -> None:
    doc = ParsedDocument.build(source="text", drafts=[], page_count=None)
    assert chunk_document(doc) == []


def test_reference_list_not_chunked() -> None:
    # Văn bản pypdfium2 giữ danh mục trong thân bài (GROBID thì tách riêng): tên các công trình
    # trong danh mục không được thành "bằng chứng" khi truy xuất.
    drafts = [
        _heading("1 Introduction"),
        _draft(_paragraph(0, 3), "1 Introduction"),
        _heading("References", page=2),
        _draft("[1] Devlin J. et al. BERT: Pre-training of deep transformers.", "References"),
        _heading("Appendix A", page=3),
        _draft(_paragraph(3, 2), "Appendix A", page=3),
    ]
    doc = ParsedDocument.build(source="pdfium", drafts=drafts, page_count=3)
    chunks = chunk_document(doc)
    assert [c.section for c in chunks] == ["1 Introduction", "Appendix A"]
    assert all("BERT" not in c.text for c in chunks)


def _words_sentence(tag: str, words: int) -> str:
    return " ".join([f"{tag}{i}" for i in range(words - 1)] + [f"{tag}end."])


def test_no_chunk_is_only_the_overlap_sentence() -> None:
    # Hồi quy (review Task 13): câu chồng lấn kết thúc đoạn và tự đủ `min_tokens` không được
    # thành một chunk riêng nằm gọn trong chunk trước; mỗi chunk phải có ít nhất một câu mới.
    long_sentence = _words_sentence("Sigma", 200)
    first = _words_sentence("Alpha", 50) + " " + long_sentence
    second = " ".join(_words_sentence(f"Pk{k}x", 30) for k in range(10))
    doc = ParsedDocument.build(
        source="pdfium",
        drafts=[_draft(first, "X"), _draft(second, "X")],
        page_count=1,
    )
    text = doc.text()
    chunks = chunk_document(doc)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text
        assert estimate_tokens(chunk.text) <= 400
    for current, following in itertools.pairwise(chunks):
        assert following.char_start >= current.char_start
        assert following.char_end > current.char_end  # không nằm gọn trong chunk trước
    # Chồng lấn vẫn đúng một câu: chunk 2 bắt đầu bằng câu dài, rồi sang đoạn sau.
    assert chunks[0].text.endswith(long_sentence)
    assert chunks[1].text.startswith(long_sentence + "\n\nPk0x0 ")


def test_random_documents_keep_invariants() -> None:
    # Câu dài ngắn ngẫu nhiên (seed cố định), kể cả câu > 400 token: lát cắt đúng, ≤ 400 token,
    # mỗi chunk tiến lên so với chunk trước và mọi từ đều nằm trong ít nhất một chunk.
    rng = random.Random(0)
    for doc_number in range(200):
        drafts = []
        counter = itertools.count()
        for _ in range(rng.randint(1, 5)):
            sentences = [
                " ".join(f"d{doc_number}w{next(counter)}" for _ in range(rng.randint(1, 300)))
                + " end."
                for _ in range(rng.randint(1, 8))
            ]
            capitalised = [s[0].upper() + s[1:] for s in sentences]
            drafts.append(_draft(" ".join(capitalised), "Body"))
        drafts.append(_draft(" ".join(["x"] * rng.randint(1, 900)) + ".", "Body"))
        doc = ParsedDocument.build(source="pdfium", drafts=drafts, page_count=1)
        text = doc.text()
        chunks = chunk_document(doc)
        for chunk in chunks:
            assert text[chunk.char_start : chunk.char_end] == chunk.text
            assert estimate_tokens(chunk.text) <= 400
        for current, following in itertools.pairwise(chunks):
            assert current.char_start <= following.char_start
            assert following.char_end > current.char_end
            # Chồng lấn, hoặc nối tiếp chỉ cách nhau khoảng trắng: không bỏ sót chữ nào.
            assert not text[current.char_end : following.char_start].strip()
        covered = {w for chunk in chunks for w in chunk.text.split()}
        assert covered == set(text.split())
