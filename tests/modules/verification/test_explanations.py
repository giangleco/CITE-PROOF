from citeproof.core.enums import ClaimStatus, UnverifiableReason
from citeproof.modules.verification import explanations
from citeproof.modules.verification.explanations import (
    deterministic_explanation,
    downgrade_note,
    explanation_language,
    safe_texts,
    status_explanation,
)
from citeproof.web.labels import FORBIDDEN_WORDS


def test_explanations_complete_and_clean() -> None:
    texts = [
        deterministic_explanation(r, lang) for r in UnverifiableReason for lang in ("vi", "en")
    ]
    texts += [
        downgrade_note(rule, lang)
        for rule in ("QUOTE_NOT_VERBATIM", "EVIDENCE_REQUIRED", "ABSTRACT_RULE")
        for lang in ("vi", "en")
    ]
    texts += [
        status_explanation(status, lang)
        for status in ClaimStatus
        if status is not ClaimStatus.UNVERIFIABLE
        for lang in ("vi", "en")
    ]
    for t in texts:
        assert t.strip()
        assert not any(w in t.lower() for w in FORBIDDEN_WORDS)


def test_explanation_language() -> None:
    assert explanation_language("en") == "en"
    assert explanation_language("vi") == "vi"
    assert explanation_language("fr") == "vi"


def test_forbidden_words_mirror_labels() -> None:
    assert explanations.FORBIDDEN_WORDS == FORBIDDEN_WORDS


def test_safe_texts_replaces_forbidden_explanation() -> None:
    ok = safe_texts(ClaimStatus.SUPPORTED, None, "Nguồn nêu rõ.", "Chỉ đọc abstract.", "vi")
    assert ok == ("Nguồn nêu rõ.", "Chỉ đọc abstract.")
    text, limits = safe_texts(
        ClaimStatus.CONTRADICTED, None, "Số liệu GIẢ MẠO.", "Có thể là đạo văn.", "en"
    )
    assert text == status_explanation(ClaimStatus.CONTRADICTED, "en")
    assert limits is None
    text, _ = safe_texts(
        ClaimStatus.UNVERIFIABLE,
        UnverifiableReason.ABSTRACT_ONLY_INSUFFICIENT,
        "Tài liệu không tồn tại.",
        None,
        "vi",
    )
    assert text == deterministic_explanation(UnverifiableReason.ABSTRACT_ONLY_INSUFFICIENT, "vi")
