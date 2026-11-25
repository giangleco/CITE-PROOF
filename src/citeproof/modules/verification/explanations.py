"""Văn bản giải thích tất định cho người dùng (vi/en). Không dùng từ cấm (`FORBIDDEN_WORDS`).

`FORBIDDEN_WORDS` chép từ `web.labels` (test giữ hai bản trùng nhau) để worker không phải nạp
tầng web. Giải thích của LLM chứa từ cấm được thay bằng văn bản tất định (`safe_texts`).
"""

import unicodedata
from typing import Literal

from citeproof.core.enums import ClaimStatus, UnverifiableReason

__all__ = [
    "FORBIDDEN_WORDS",
    "ExplanationLanguage",
    "contains_forbidden",
    "deterministic_explanation",
    "downgrade_note",
    "explanation_language",
    "safe_texts",
    "status_explanation",
]

FORBIDDEN_WORDS = ("bịa đặt", "giả mạo", "không tồn tại", "gian lận", "đạo văn")

ExplanationLanguage = Literal["vi", "en"]

_EXPLANATIONS: dict[UnverifiableReason, dict[ExplanationLanguage, str]] = {
    UnverifiableReason.REFERENCE_UNRESOLVED: {
        "vi": "Chưa xác định được tài liệu tham khảo mà trích dẫn này trỏ tới.",
        "en": "The reference this citation points to could not be identified.",
    },
    UnverifiableReason.REFERENCE_NOT_FOUND: {
        "vi": "Không tìm thấy tài liệu này trong các nguồn đã tra cứu.",
        "en": "This reference was not found in the sources searched.",
    },
    UnverifiableReason.NO_ACCESSIBLE_TEXT: {
        "vi": "Chưa có văn bản của nguồn để đối chiếu; bạn có thể tải tệp PDF lên.",
        "en": "No text of the source is available to check against; you can upload the PDF.",
    },
    UnverifiableReason.ABSTRACT_ONLY_INSUFFICIENT: {
        "vi": "Chỉ có phần tóm tắt của nguồn, chưa đủ để kết luận nhận định không được hỗ trợ.",
        "en": "Only the source abstract is available, which is not enough to conclude "
        "the claim is unsupported.",
    },
    UnverifiableReason.SOURCE_PARSE_FAILED: {
        "vi": "Không đọc được nội dung tệp nguồn nên chưa đối chiếu được.",
        "en": "The source file could not be read, so the claim could not be checked.",
    },
    UnverifiableReason.EVIDENCE_INCONCLUSIVE: {
        "vi": "Các đoạn tìm được chưa đủ rõ để kết luận; nên tự kiểm tra nguồn.",
        "en": "The passages found are not clear enough to conclude; please check the source.",
    },
    UnverifiableReason.SOURCE_LANGUAGE_UNSUPPORTED: {
        "vi": "Nguồn viết bằng ngôn ngữ chưa được hỗ trợ (chỉ hỗ trợ tiếng Việt và tiếng Anh).",
        "en": "The source is in an unsupported language (only Vietnamese and English).",
    },
    UnverifiableReason.NOT_A_CLAIM: {
        "vi": "Câu này không phải nhận định cần kiểm chứng với nguồn.",
        "en": "This sentence is not a claim that needs checking against a source.",
    },
    UnverifiableReason.PROCESSING_ERROR: {
        "vi": "Có lỗi trong quá trình xử lý nên chưa kiểm chứng được nhận định này.",
        "en": "A processing error prevented this claim from being checked.",
    },
}

_STATUS_EXPLANATIONS: dict[ClaimStatus, dict[ExplanationLanguage, str]] = {
    ClaimStatus.SUPPORTED: {
        "vi": "Nguồn có đoạn nêu rõ điều nhận định này khẳng định; xem trích đoạn bên dưới.",
        "en": "The source has a passage that states what this claim asserts; see the quote below.",
    },
    ClaimStatus.PARTIALLY_SUPPORTED: {
        "vi": "Nguồn chỉ hỗ trợ một phần nhận định này; xem trích đoạn bên dưới.",
        "en": "The source supports only part of this claim; see the quote below.",
    },
    ClaimStatus.CONTRADICTED: {
        "vi": "Nguồn có đoạn nói khác với nhận định này; xem trích đoạn bên dưới.",
        "en": "The source has a passage that differs from this claim; see the quote below.",
    },
    ClaimStatus.UNSUPPORTED: {
        "vi": "Các đoạn đã đọc của nguồn không có nội dung hỗ trợ nhận định này.",
        "en": "The passages read from the source do not support this claim.",
    },
}

_NOTES: dict[str, dict[ExplanationLanguage, str]] = {
    "QUOTE_NOT_VERBATIM": {
        "vi": "Hệ thống đã bỏ các trích đoạn không khớp nguyên văn với nguồn, "
        "nên chưa đủ bằng chứng để kết luận.",
        "en": "Quotes that do not match the source verbatim were discarded, "
        "so there is not enough evidence to conclude.",
    },
    "EVIDENCE_REQUIRED": {
        "vi": "Kết luận ban đầu thiếu bằng chứng phù hợp nên được chuyển về chưa kết luận.",
        "en": "The initial conclusion lacked matching evidence and was set to inconclusive.",
    },
    "ABSTRACT_RULE": {
        "vi": "Chỉ có phần tóm tắt nên chưa thể kết luận nhận định không được hỗ trợ.",
        "en": "Only the abstract was available, so the claim cannot be concluded unsupported.",
    },
}


def explanation_language(document_language: str) -> ExplanationLanguage:
    return "en" if document_language == "en" else "vi"


def deterministic_explanation(reason: UnverifiableReason, language: ExplanationLanguage) -> str:
    return _EXPLANATIONS[reason][language]


def downgrade_note(rule: str, language: ExplanationLanguage) -> str:
    return _NOTES[rule][language]


def status_explanation(status: ClaimStatus, language: ExplanationLanguage) -> str:
    """Giải thích tất định theo trạng thái kết luận (khác `UNVERIFIABLE`)."""
    return _STATUS_EXPLANATIONS[status][language]


def contains_forbidden(text: str | None) -> bool:
    if not text:
        return False
    # NFC trước: dấu tách rời (NFD) không được lọt qua phép so khớp.
    lowered = unicodedata.normalize("NFC", text).lower()
    return any(word in lowered for word in FORBIDDEN_WORDS)


def safe_texts(
    status: ClaimStatus,
    reason: UnverifiableReason | None,
    explanation: str,
    limitations: str | None,
    language: ExplanationLanguage,
) -> tuple[str, str | None]:
    """`(explanation, limitations)` không chứa từ cấm.

    Giải thích chứa từ cấm được thay bằng văn bản tất định của kết quả (theo lý do khi
    `UNVERIFIABLE`, theo trạng thái khi khác); giới hạn chứa từ cấm bị bỏ.
    """
    if contains_forbidden(explanation):
        if status is ClaimStatus.UNVERIFIABLE:
            explanation = deterministic_explanation(
                reason or UnverifiableReason.EVIDENCE_INCONCLUSIVE, language
            )
        else:
            explanation = status_explanation(status, language)
    if contains_forbidden(limitations):
        limitations = None
    return explanation, limitations
