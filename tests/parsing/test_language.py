"""Heuristic ngôn ngữ theo đoạn, theo tài liệu và cho nguồn (spec §7.1, §7.8)."""

import unicodedata

import pytest

from citeproof.core.enums import Language
from citeproof.parsing.language import detect_language, document_language, source_language
from citeproof.parsing.model import ParagraphDraft, ParsedDocument

VI = "Mô hình ngôn ngữ huấn luyện trước đã thay đổi cách xử lý văn bản tiếng Việt hiện nay."
EN = "Pre-trained language models have changed the way that we process text in many tasks."


def _doc(*texts: str) -> ParsedDocument:
    drafts = [ParagraphDraft(text=text, section=None, page=None) for text in texts]
    return ParsedDocument.build(source="text", drafts=drafts, page_count=None)


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("Mô hình này đạt độ chính xác cao hơn.", "vi"),
        ("The model achieves higher accuracy.", "en"),
        ("12345 67", "unknown"),
    ],
)
def test_detect_language(text: str, lang: str) -> None:
    assert detect_language(text) is Language(lang)


def test_detect_language_accepts_nfd() -> None:
    assert detect_language(unicodedata.normalize("NFD", VI)) is Language.VI


def test_detect_language_needs_english_function_words() -> None:
    # Danh sách tên không dấu: không đủ bằng chứng là tiếng Anh.
    assert detect_language("Vaswani A., Shazeer N., Parmar N., Uszkoreit J., Jones L.") is (
        Language.UNKNOWN
    )
    assert detect_language(EN) is Language.EN


def test_legacy_encoded_text_is_not_vietnamese() -> None:
    # Câu tiếng Việt bảng mã TCVN3 đọc như Latin-1: chữ Latin-1 có dấu nhưng không phải tiếng Việt.
    mojibake = "HÖ thèng kiÓm tra trÝch dÉn tù ®éng cho luËn v¨n tiÕng ViÖt."
    assert detect_language(mojibake) is Language.UNKNOWN
    assert detect_language("Vieät Nam laø moät nöôùc coù neàn vaên hoùa laâu ñôøi.") is (
        Language.UNKNOWN
    )


def test_document_language_mixed() -> None:
    doc = _doc(VI, VI, VI, EN, EN)
    assert document_language(doc.paragraphs) is Language.MIXED
    assert doc.language is Language.MIXED


def test_document_language_majority_and_unknown() -> None:
    assert _doc(VI, VI, VI, VI, VI, "The model works well.").language is Language.VI
    assert _doc(EN, EN, VI).language is Language.MIXED
    assert _doc(EN, EN, EN, EN, EN, VI).language is Language.EN
    assert _doc("12345", "[1] [2]").language is Language.UNKNOWN
    assert document_language([]) is Language.UNKNOWN


def test_source_language_other() -> None:
    assert source_language("Модель достигает более высокой точности на тестовом наборе.") == (
        "other"
    )
    assert source_language("该模型在测试集上取得了更高的准确率并且训练更加稳定。") == "other"
    assert source_language(EN) == "en"
    assert source_language(VI) == "vi"
    assert source_language("") == "unknown"
