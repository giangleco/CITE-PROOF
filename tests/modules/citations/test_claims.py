"""Span claim tất định (QĐ-16) và bản nháp claim của S4 (spec §7.4; F41)."""

import uuid

import pytest

from citeproof.core.enums import ChunkRegion, LinkMethod
from citeproof.modules.citations.claims import (
    ClaimDraft,
    build_drafts,
    context_text,
    deterministic_span,
)
from citeproof.modules.citations.linking import LinkTarget, ReferenceIndex
from citeproof.modules.citations.markers import find_markers
from citeproof.modules.citations.sentences import Sentence, split_sentences
from citeproof.parsing.model import ParagraphDraft, ParsedDocument

EDGE_PUNCTUATION = set(".,;:!?…-\u2013— \n\t")

SENTENCES = [
    "Các mô hình học sâu đạt kết quả tốt [1].",
    "A improves B [1], while C degrades D [2].",
    "Mô hình [3] được đề xuất để phân loại văn bản tiếng Việt.",
    "[4] cho rằng dữ liệu là quan trọng nhất.",
    "Xem thêm [5].",
    "[6].",
    "Kiến trúc Transformer (Vaswani et al., 2017) dựa trên tự chú ý, và BERT (Devlin et al., 2019)",
    "Nguyễn Văn An (2020) cho rằng mô hình A tốt hơn mô hình B.",
    "Theo Nguyễn và cs. (2020), mô hình A tốt hơn.",
    "Prior work [1], [2] shows a gain; later work [3]\u2013[5] confirms it [6].",
    'Mô hình ngôn ngữ ("BERT") rất mạnh [7]!',
    "Kết quả (xem phụ lục [8]) cho thấy độ chính xác 3.5 điểm [9].",
    "Self-attention based models have become the standard for sequence transduction\n[1].",
    "Mô hình (BERT) [1].",
    "Mô hình đạt kết quả tốt trên tập dữ liệu [1, tr. 25; 3, tr. 7].",
    "Theo giáo trình [2: 25-30], khái niệm này được định nghĩa rõ ràng [4, trang 12].",
]


def _all_spans(sentence: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    previous: int | None = None
    for marker in find_markers(sentence):
        spans.append(deterministic_span(sentence, marker, previous))
        previous = marker.end
    return spans


@pytest.mark.parametrize("sentence", SENTENCES)
def test_deterministic_span_is_substring(sentence: str) -> None:
    spans = _all_spans(sentence)
    assert spans, sentence
    for start, end in spans:
        assert 0 <= start < end <= len(sentence)
        claim = sentence[start:end]
        assert claim == claim.strip()
        assert claim[0] not in EDGE_PUNCTUATION and claim[-1] not in EDGE_PUNCTUATION
        # Ngoặc ở hai đầu luôn cân: không cắt dở "(BERT" hay "phụ lục [8]".
        assert claim.count("(") == claim.count(")")
        assert claim.count('"') % 2 == 0


def test_span_two_markers_in_one_sentence() -> None:
    sentence = "A improves B [1], while C degrades D [2]."
    assert [sentence[s:e] for s, e in _all_spans(sentence)] == [
        "A improves B",
        "while C degrades D",
    ]


def test_short_span_falls_back_to_sentence_without_edge_markers() -> None:
    sentence = "Mô hình [3] được đề xuất để phân loại văn bản tiếng Việt."
    (span,) = _all_spans(sentence)
    assert sentence[span[0] : span[1]] == sentence[:-1]  # marker giữa câu được giữ nguyên
    sentence = "[4] cho rằng dữ liệu là quan trọng nhất."
    (span,) = _all_spans(sentence)
    assert sentence[span[0] : span[1]] == "cho rằng dữ liệu là quan trọng nhất"
    whole = "Prior work [1], [2] shows a gain; later work [3]\u2013[5] confirms it"
    sentence = f"{whole} [6]."
    texts = [sentence[s:e] for s, e in _all_spans(sentence)]
    # "Prior work" và "confirms it" dưới 3 từ: lấy cả câu bỏ marker cuối.
    assert texts == [whole, whole, "shows a gain; later work", whole]


def test_span_only_marker_sentence_is_not_empty() -> None:
    sentence = "[6]."
    (span,) = _all_spans(sentence)
    assert sentence[span[0] : span[1]] == "[6]"


def test_span_author_year_and_narrative() -> None:
    sentence = "Nguyễn Văn An (2020) cho rằng mô hình A tốt hơn mô hình B."
    (span,) = _all_spans(sentence)
    assert sentence[span[0] : span[1]] == "cho rằng mô hình A tốt hơn mô hình B"
    sentence = "Theo Nguyễn và cs. (2020), mô hình A tốt hơn."
    (span,) = _all_spans(sentence)
    assert sentence[span[0] : span[1]] == "Theo Nguyễn và cs. (2020), mô hình A tốt hơn"


def test_context_text() -> None:
    sentences = split_sentences("Một. Hai [1]. Ba.")
    assert context_text(sentences, 1) == "Một. Ba."
    assert context_text(sentences, 0) == "Hai [1]."
    assert context_text(sentences, 2) == "Hai [1]."
    assert context_text([Sentence(text="Duy nhất [1].", start=0, end=13)], 0) == ""


def _doc(*paragraphs: ParagraphDraft) -> ParsedDocument:
    return ParsedDocument.build(source="docx", drafts=list(paragraphs), page_count=None)


def _target(order_index: int) -> LinkTarget:
    return LinkTarget(
        entry_id=uuid.uuid4(),
        order_index=order_index,
        label=None,
        authors=(),
        year=None,
        year_suffix=None,
    )


async def test_build_drafts_skips_headings_captions_and_reference_block() -> None:
    doc = _doc(
        ParagraphDraft(text="Chương 1 [1]", section=None, page=1, kind="heading"),
        ParagraphDraft(
            text="Mở đầu. Mô hình học sâu đạt kết quả tốt trên dữ liệu [1]. Cuối.",
            section="Chương 1",
            page=1,
        ),
        ParagraphDraft(
            text="Hình 1. Kiến trúc mô hình [2].",
            section="Chương 1",
            page=2,
            region=ChunkRegion.FIGURE,
            kind="caption",
        ),
        ParagraphDraft(text="Tài liệu tham khảo", section=None, page=3, kind="heading"),
        ParagraphDraft(text="[1] Nguyễn Văn An (2020), Tên bài.", section=None, page=3),
    )
    targets = [_target(0)]
    drafts = await build_drafts(doc, ReferenceIndex(targets))
    assert len(drafts) == 1
    draft, links = drafts[0]
    assert isinstance(draft, ClaimDraft)
    assert links == [targets[0].entry_id]
    assert draft.paragraph_index == 1
    assert draft.marker.text == "[1]" and draft.marker.kind is LinkMethod.NUMERIC
    assert draft.sentence.text == "Mô hình học sâu đạt kết quả tốt trên dữ liệu [1]."
    assert draft.claim_text == "Mô hình học sâu đạt kết quả tốt trên dữ liệu"
    assert draft.claim_text == draft.sentence.text[draft.span_start : draft.span_end]
    assert draft.is_claim is True
    assert draft.extraction_method == "rule"
    assert draft.language == "vi"
    assert draft.context_text == "Mở đầu. Cuối."
    paragraph = doc.paragraphs[draft.paragraph_index]
    position = paragraph.char_start + draft.sentence.start + draft.marker.start
    assert doc.text()[position : position + len(draft.marker.text)] == "[1]"


async def test_build_drafts_dedupes_link_targets_per_marker() -> None:
    doc = _doc(
        ParagraphDraft(text="Hai lần cùng một nguồn [1, 1, 2] và [7, 8].", section=None, page=None)
    )
    targets = [_target(0), _target(1)]
    drafts = await build_drafts(doc, ReferenceIndex(targets))
    assert [links for _, links in drafts] == [
        [targets[0].entry_id, targets[1].entry_id],
        [None],  # hai số ngoài khoảng: một link NULL (khóa duy nhất của S8, F41)
    ]


async def test_build_drafts_language_of_sentence() -> None:
    doc = _doc(
        ParagraphDraft(
            text=(
                "Mô hình tiếng Việt đạt độ chính xác cao trên tập dữ liệu [1]. "
                "The model is also strong on the English benchmark [1]."
            ),
            section=None,
            page=None,
        )
    )
    drafts = await build_drafts(doc, ReferenceIndex([_target(0)]))
    assert [draft.language for draft, _ in drafts] == ["vi", "en"]
