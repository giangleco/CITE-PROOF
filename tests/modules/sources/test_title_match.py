from citeproof.core.enums import TitleMatch
from citeproof.modules.sources.title_match import TITLE_MATCH_THRESHOLD, title_match
from tests.files import titled_pdf

TITLE = "Attention Is All You Need"


def test_threshold() -> None:
    assert TITLE_MATCH_THRESHOLD == 85


def test_title_match_cases() -> None:
    pdf = titled_pdf(TITLE)
    assert title_match(pdf, TITLE) is TitleMatch.MATCH
    assert title_match(pdf, "attention is all you need.") is TitleMatch.MATCH
    assert title_match(pdf, "BERT: Pre-training of Deep Bidirectional Transformers") is (
        TitleMatch.MISMATCH
    )
    assert title_match(pdf, None) is TitleMatch.UNKNOWN
    assert title_match(pdf, "  ") is TitleMatch.UNKNOWN


def test_title_from_page_text_without_metadata() -> None:
    assert title_match(titled_pdf(TITLE, metadata=False), TITLE) is TitleMatch.MATCH


def test_title_only_in_metadata() -> None:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    canvas.setTitle(TITLE)
    canvas.drawString(72, 700, "Body text without the heading.")
    canvas.showPage()
    canvas.save()
    assert title_match(buffer.getvalue(), TITLE) is TitleMatch.MATCH


def test_vietnamese_diacritics_folded() -> None:
    pdf = titled_pdf("Kiem chung trich dan")
    assert title_match(pdf, "Kiểm chứng trích dẫn") is TitleMatch.MATCH


def _pdf(*, title: str | None, text: str | None) -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    if title:
        canvas.setTitle(title)
    if text:
        canvas.drawString(72, 700, text)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def test_text_shorter_than_title_is_unknown() -> None:
    # Tiêu đề metadata chỉ là đầu của tiêu đề reference: không được MATCH oan.
    assert title_match(_pdf(title="Attention", text=None), TITLE) is TitleMatch.UNKNOWN


def test_empty_text_is_unknown() -> None:
    assert title_match(_pdf(title=None, text=None), TITLE) is TitleMatch.UNKNOWN
