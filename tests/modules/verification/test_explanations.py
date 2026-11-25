from citeproof.core.enums import UnverifiableReason
from citeproof.modules.verification.explanations import (
    deterministic_explanation,
    downgrade_note,
    explanation_language,
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
    for t in texts:
        assert t.strip()
        assert not any(w in t.lower() for w in FORBIDDEN_WORDS)


def test_explanation_language() -> None:
    assert explanation_language("en") == "en"
    assert explanation_language("vi") == "vi"
    assert explanation_language("fr") == "vi"
