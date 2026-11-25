import unicodedata

from citeproof.modules.verification.quotes import locate_quote


def test_quote_whitespace_insensitive() -> None:
    text = "x a b c y"
    span = locate_quote("a  b\nc", text)
    assert span is not None
    assert text[span[0] : span[1]] == "a b c"


def test_offsets_index_original_text_with_whitespace_runs() -> None:
    text = "x a \n  b   c y"
    span = locate_quote("a b c", text)
    assert span is not None
    assert text[span[0] : span[1]] == "a \n  b   c"


def test_quote_matches_across_nfc_nfd() -> None:
    nfc = "Chúng tôi dùng Mô hình ngôn ngữ lớn."
    nfd = unicodedata.normalize("NFD", "Mô hình")
    assert nfd != "Mô hình"
    span = locate_quote(nfd, nfc)
    assert span is not None
    assert nfc[span[0] : span[1]] == "Mô hình"
    # chunk ở NFD, quote ở NFC: offset vẫn chỉ vào văn bản gốc
    chunk = unicodedata.normalize("NFD", nfc)
    span2 = locate_quote("Mô hình", chunk)
    assert span2 is not None
    assert unicodedata.normalize("NFC", chunk[span2[0] : span2[1]]) == "Mô hình"


def test_quote_changed_char_rejected() -> None:
    assert locate_quote("accuracy 95.1%", "accuracy 95.7%") is None


def test_quote_with_ellipsis_rejected() -> None:
    assert locate_quote("A … C", "A B C") is None
    assert locate_quote("A ... C", "A B C") is None


def test_empty_quote_rejected() -> None:
    assert locate_quote("  \n ", "abc") is None


def test_leading_whitespace_runs() -> None:
    for text, start in (("  abc", 2), ("\n\nabc", 2), (" \n \t abc", 5)):
        assert locate_quote("abc", text) == (start, start + 3)


def test_trailing_whitespace_runs() -> None:
    for text in ("abc  ", "abc\n\n", "abc \n \t "):
        assert locate_quote("abc", text) == (0, 3)
    assert locate_quote("abc", "  ") is None
    assert locate_quote("abc", "") is None


def test_mixed_nfd_nfc_quote_exact_offsets() -> None:
    text = "Dùng Mô hình"
    quote = unicodedata.normalize("NFD", "Mô ") + "hình"
    assert quote != "Mô hình"
    assert locate_quote(quote, text) == (5, 12)
    assert locate_quote(quote, unicodedata.normalize("NFD", text)) is not None


def test_unescape_quote_inverts_escape_data() -> None:
    from citeproof.llm.context import escape_data
    from citeproof.modules.verification.quotes import unescape_quote

    raw = "p < 0.05 & R&D > x &lt; literal"
    assert unescape_quote(escape_data(raw)) == raw
