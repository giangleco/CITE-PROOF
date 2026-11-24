"""TEI của GROBID → ParsedDocument và ParsedReference (spec §7.1, QĐ-03)."""

import pytest

from citeproof.core.enums import ChunkRegion, Language
from citeproof.core.errors import ParseError
from citeproof.parsing.model import ParsedDocument
from citeproof.parsing.tei import parse_citation_list_tei, parse_fulltext_tei
from tests.golden.cases import GOLDEN_DIR, GoldenExpected

TEI_NS = 'xmlns="http://www.tei-c.org/ns/1.0"'


def _recorded(name: str, kind: str = "fulltext") -> str:
    return (GOLDEN_DIR / f"{name}.{kind}.tei.xml").read_text(encoding="utf-8")


def _expected(name: str) -> GoldenExpected:
    path = GOLDEN_DIR / f"{name}.expected.json"
    return GoldenExpected.model_validate_json(path.read_text(encoding="utf-8"))


def _assert_offsets(doc: ParsedDocument) -> None:
    text = doc.text()
    for paragraph in doc.paragraphs:
        assert text[paragraph.char_start : paragraph.char_end] == paragraph.text


def test_tei_sections_pages_offsets() -> None:
    doc = parse_fulltext_tei(_recorded("en_ieee"), page_count=2)
    assert doc.source == "grobid"
    assert doc.page_count == 2
    assert doc.language is Language.EN
    _assert_offsets(doc)
    headings = [p for p in doc.paragraphs if p.kind == "heading"]
    assert [(h.text, h.section, h.page) for h in headings] == [
        ("I. Introduction", "I. Introduction", 1),
        ("II. Method", "II. Method", 1),
        ("III. Results", "III. Results", 2),
    ]
    abstract = doc.paragraphs[0]
    assert abstract.section == "Abstract"
    assert abstract.kind == "body"
    assert abstract.page == 1
    # Câu (<s>) nối bằng một khoảng trắng; marker <ref> giữ nguyên trong câu.
    assert abstract.text.startswith("We study how sequence models")
    assert "sources. Experiments" in abstract.text
    intro = [p for p in doc.paragraphs if p.section == "I. Introduction" and p.kind == "body"]
    first_body = intro[0]
    assert first_body.page == 1
    assert "sequence transduction [1]." in first_body.text
    results = [p for p in doc.paragraphs if p.section == "III. Results"]
    assert {p.page for p in results} == {2}
    (caption,) = [p for p in doc.paragraphs if p.kind == "caption"]
    assert caption.region is ChunkRegion.TABLE
    assert caption.section == "III. Results"
    assert caption.page == 2
    assert caption.text.endswith("Accuracy of the baseline and the fine-tuned encoder.")
    assert caption.text.startswith("TABLE I")
    # Bảng chỉ lấy chú thích, không lấy ô; danh mục không nằm trong thân bài.
    assert "Baseline LSTM" not in doc.text()
    assert "Hochreiter" not in doc.text()


def test_tei_references_from_listbibl() -> None:
    expected = _expected("en_ieee")
    doc = parse_fulltext_tei(_recorded("en_ieee"), page_count=2)
    assert doc.references_raw is not None
    assert len(doc.references_raw) == len(expected.references)
    for ref, want in zip(doc.references_raw, expected.references, strict=True):
        assert ref.parsed is not None
        assert ref.parsed.title == want.title
        assert ref.parsed.year == want.year
        assert ref.parsed.doi == want.doi
        assert ref.parsed.authors[0].split()[-1] == want.first_author_family
        assert ref.page == 2
        assert ref.label is None  # GROBID bỏ nhãn [n] khỏi chuỗi thô
        assert not ref.raw.startswith("[")
    first = doc.references_raw[0]
    assert first.raw.startswith("A. Vaswani et al.")
    assert first.parsed is not None and first.parsed.doi == "10.48550/arXiv.1706.03762"
    second = doc.references_raw[1].parsed
    assert second is not None
    assert second.authors == ["J Devlin", "M.-W Chang", "K Lee", "K Toutanova"]
    assert second.venue == "Proc. NAACL-HLT"
    fourth = doc.references_raw[3].parsed
    assert fourth is not None and fourth.venue == "Neural Comput"


def test_tei_numbered_heads_and_abstract_divs() -> None:
    # GROBID đưa cả phần mở đầu vào <abstract>; <head n="1."> trong đó vẫn đổi mục.
    doc = parse_fulltext_tei(_recorded("vi_mixed_apa"), page_count=None)
    assert doc.page_count == 2  # đếm <surface> khi không truyền số trang
    assert doc.language is Language.VI
    assert doc.references_raw == []
    assert [p.section for p in doc.paragraphs] == [
        "Abstract",
        "1. Giới thiệu",
        "1. Giới thiệu",
        "2. Cơ sở lý thuyết",
        "2. Cơ sở lý thuyết",
    ]
    assert "(Vaswani et al., 2017)" in doc.text()  # khoảng trắng thừa trong TEI bị gộp
    _assert_offsets(doc)


def test_tei_pages_from_first_coords() -> None:
    doc = parse_fulltext_tei(_recorded("vi_scanned_pages"), page_count=3)
    assert [(p.section, p.page) for p in doc.paragraphs] == [
        ("Abstract", 1),
        ("Abstract", 1),
        ("Abstract", 3),
        ("Abstract", 3),
    ]


def test_tei_figure_footnote_and_back_divs() -> None:
    tei = f"""<TEI {TEI_NS}><text><body>
      <div><head n="2.1">Kết quả</head>
        <p coords="4,1,1,1,1">Độ chính xác tăng rõ rệt.</p>
        <figure xml:id="fig_0" coords="5,1,1,1,1"><head>Hình 1.</head>
          <figDesc>Kiến trúc tổng quát.</figDesc></figure>
        <list><item coords="5,2,2,2,2">Mục một của danh sách.</item></list>
      </div>
      <note place="foot" n="1" coords="5,3,3,3,3">Ghi chú chân trang.</note>
    </body><back>
      <div type="annex"><div><head>Phụ lục A</head><p>Bộ câu hỏi khảo sát.</p></div></div>
      <div type="references"><listBibl/></div>
    </back></text></TEI>"""
    doc = parse_fulltext_tei(tei, page_count=6)
    rows = [(p.kind, p.region, p.section, p.page, p.text) for p in doc.paragraphs]
    assert rows == [
        ("heading", ChunkRegion.BODY, "2.1 Kết quả", None, "2.1 Kết quả"),
        ("body", ChunkRegion.BODY, "2.1 Kết quả", 4, "Độ chính xác tăng rõ rệt."),
        ("caption", ChunkRegion.FIGURE, "2.1 Kết quả", 5, "Hình 1. Kiến trúc tổng quát."),
        ("body", ChunkRegion.BODY, "2.1 Kết quả", 5, "Mục một của danh sách."),
        ("body", ChunkRegion.OTHER, "2.1 Kết quả", 5, "Ghi chú chân trang."),
        ("heading", ChunkRegion.BODY, "Phụ lục A", None, "Phụ lục A"),
        ("body", ChunkRegion.BODY, "Phụ lục A", None, "Bộ câu hỏi khảo sát."),
    ]
    assert doc.references_raw == []


@pytest.mark.parametrize(
    "tei",
    [
        '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><TEI>&e;</TEI>',
        '<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><TEI>&b;</TEI>',
        '<!DOCTYPE TEI SYSTEM "http://example.org/tei.dtd"><TEI/>',
    ],
)
def test_tei_with_entity_rejected(tei: str) -> None:
    with pytest.raises(ParseError) as caught:
        parse_fulltext_tei(tei, page_count=None)
    assert caught.value.code == "TEI_REJECTED"
    with pytest.raises(ParseError) as again:
        parse_citation_list_tei(tei)
    assert again.value.code == "TEI_REJECTED"


def test_tei_malformed_rejected() -> None:
    with pytest.raises(ParseError) as caught:
        parse_fulltext_tei("<TEI><text>", page_count=None)
    assert caught.value.code == "TEI_INVALID"


def test_citation_list_tei() -> None:
    refs = parse_citation_list_tei(_recorded("vi_thesis_numeric", "citations"))
    expected = _expected("vi_thesis_numeric")
    assert len(refs) == len(expected.references)
    assert refs[0].title == "Phân loại văn bản tiếng Việt bằng mạng nơ-ron tích chập"
    assert refs[0].year == 2020
    assert refs[0].venue == "Tạp chí Khoa học và Công nghệ Mẫu"
    # Tên Việt giữ thứ tự đọc (họ trước) để S2 lấy được họ.
    assert refs[0].authors[0] == "Nguyễn Văn"
    assert all(ref.label is None for ref in refs)
    assert [ref.doi for ref in refs] == [None, None, "10.48550/arXiv.1706.03762", None]
    assert [ref.year for ref in refs] == [want.year for want in expected.references]
    apa = parse_citation_list_tei(_recorded("en_paper_author_year", "citations"))
    assert any(ref.url == "https://doi.org/10.18653/v1/N19-1423" for ref in apa)
