"""Nhãn tiếng Việt cho mọi trạng thái, chép nguyên văn từ `.stitch/DESIGN.md` §8.

Đây là nguồn nhãn duy nhất: template và báo cáo Markdown chỉ lấy nhãn từ đây.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    FeedbackVerdict,
    ReferenceStatus,
    RefType,
    RunStatus,
    StageName,
    UnverifiableReason,
)


@dataclass(frozen=True)
class Badge:
    label: str
    glyph: str
    tone: str  # ok | part | contra | unsup | neutral | absent | syserr


FORBIDDEN_WORDS = ("bịa đặt", "giả mạo", "không tồn tại", "gian lận", "đạo văn")

STAGES: Mapping[StageName, tuple[str, str]] = {
    StageName.S1: ("Đọc tài liệu", "Nhận diện ngôn ngữ, phát hiện trang ảnh quét"),
    StageName.S2: ("Tách danh mục tài liệu tham khảo", ""),
    StageName.S3: ("Tra cứu tài liệu tham khảo", "Crossref, OpenAlex, Semantic Scholar"),
    StageName.S4: ("Tìm trích dẫn và nhận định", ""),
    StageName.S5: ("Lấy toàn văn nguồn", "Nguồn mở"),
    StageName.S6: ("Lập chỉ mục nguồn", "Chia đoạn, giữ số trang và mục"),
    StageName.S7: ("Tìm đoạn liên quan", ""),
    StageName.S8: ("Đối chiếu nhận định", ""),
    StageName.S9: ("Tạo báo cáo", ""),
}

CLAIM_STATUS: Mapping[ClaimStatus, Badge] = {
    ClaimStatus.SUPPORTED: Badge("Được nguồn hỗ trợ", "i-ok", "ok"),
    ClaimStatus.PARTIALLY_SUPPORTED: Badge("Hỗ trợ một phần", "i-part", "part"),
    ClaimStatus.CONTRADICTED: Badge("Mâu thuẫn với nguồn", "i-contra", "contra"),
    ClaimStatus.UNSUPPORTED: Badge("Nguồn không nói điều này", "i-unsup", "unsup"),
    ClaimStatus.UNVERIFIABLE: Badge("Chưa kiểm chứng được", "i-unver", "neutral"),
}

REF_STATUS: Mapping[ReferenceStatus, Badge] = {
    ReferenceStatus.MATCHED: Badge("Đã xác minh", "i-ok", "ok"),
    ReferenceStatus.METADATA_MISMATCH: Badge("Lệch thông tin", "i-part", "part"),
    ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES: Badge(
        "Chưa tìm thấy trong nguồn đã tra", "i-absent", "absent"
    ),
    ReferenceStatus.UNSUPPORTED_TYPE: Badge(
        "Loại tài liệu chưa hỗ trợ tra cứu", "i-type", "neutral"
    ),
    ReferenceStatus.PARSE_FAILED: Badge("Chưa tách được thông tin", "i-unver", "neutral"),
    ReferenceStatus.LOOKUP_FAILED: Badge("Chưa tra được, lỗi kết nối", "i-retry", "syserr"),
}

REASONS: Mapping[UnverifiableReason, str] = {
    UnverifiableReason.REFERENCE_UNRESOLVED: (
        "Chưa xác định được trích dẫn này trỏ tới tài liệu nào trong danh mục."
    ),
    UnverifiableReason.REFERENCE_NOT_FOUND: (
        "Tài liệu được trích chưa tìm thấy trong các nguồn đã tra."
    ),
    UnverifiableReason.NO_ACCESSIBLE_TEXT: (
        "Chưa có toàn văn hay tóm tắt của nguồn. Bạn có thể tải PDF lên."
    ),
    UnverifiableReason.ABSTRACT_ONLY_INSUFFICIENT: (
        "Chỉ có tóm tắt của nguồn, và tóm tắt chưa đủ để kết luận."
    ),
    UnverifiableReason.SOURCE_PARSE_FAILED: "Chưa đọc được văn bản của nguồn.",
    UnverifiableReason.EVIDENCE_INCONCLUSIVE: "Có văn bản nguồn nhưng chưa đủ rõ để kết luận.",
    UnverifiableReason.SOURCE_LANGUAGE_UNSUPPORTED: (
        "Nguồn viết bằng ngôn ngữ chưa hỗ trợ; hiện chỉ hỗ trợ tiếng Việt và tiếng Anh."
    ),
    UnverifiableReason.NOT_A_CLAIM: "Câu này không chứa nhận định để kiểm chứng.",
    UnverifiableReason.PROCESSING_ERROR: (
        "Lỗi hệ thống khi xử lý nhận định này. Bạn có thể kiểm tra lại bài."
    ),
}

RELATIONS: Mapping[EvidenceRelation, str] = {
    EvidenceRelation.SUPPORTS: "Hỗ trợ",
    EvidenceRelation.PARTIALLY_SUPPORTS: "Hỗ trợ một phần",
    EvidenceRelation.CONTRADICTS: "Ngược lại",
    EvidenceRelation.RELATED: "Đoạn liên quan gần nhất",
}

CONTENT_LEVEL: Mapping[ContentLevelUsed, str] = {
    ContentLevelUsed.FULL_TEXT: "Toàn văn",
    ContentLevelUsed.ABSTRACT: "Tóm tắt",
    ContentLevelUsed.NONE: "Không có văn bản nguồn",
}

RUN_STATUS: Mapping[RunStatus, Badge] = {
    RunStatus.QUEUED: Badge("Đang chờ", "i-unver", "neutral"),
    RunStatus.RUNNING: Badge("Đang kiểm tra", "i-unver", "neutral"),
    RunStatus.COMPLETED: Badge("Đã xong", "i-ok", "ok"),
    RunStatus.COMPLETED_WITH_WARNINGS: Badge("Đã xong, có cảnh báo", "i-part", "part"),
    RunStatus.FAILED: Badge("Tạm dừng do lỗi", "i-retry", "syserr"),
    RunStatus.CANCELLED: Badge("Đã hủy", "i-unver", "neutral"),
}

REF_TYPES: Mapping[RefType, str] = {
    RefType.ARTICLE: "Bài báo",
    RefType.CONFERENCE: "Bài hội nghị",
    RefType.PREPRINT: "Bản tiền ấn phẩm",
    RefType.BOOK: "Sách",
    RefType.THESIS: "Luận văn, luận án",
    RefType.WEB: "Trang web",
    RefType.OTHER: "Loại khác",
    RefType.UNKNOWN: "Chưa rõ loại",
}

SOURCES: Mapping[str, str] = {
    "crossref": "Crossref",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
}

FEEDBACK: Mapping[FeedbackVerdict, str] = {
    FeedbackVerdict.AGREE: "Đồng ý",
    FeedbackVerdict.DISAGREE: "Không đồng ý",
    FeedbackVerdict.UNSURE: "Không chắc",
}

MISMATCH_FIELDS: Mapping[str, str] = {
    "title": "Tên bài",
    "authors": "Tác giả",
    "year": "Năm",
    "venue": "Nơi xuất bản",
    "doi": "DOI",
}

WARNINGS: Mapping[str, str] = {
    "SCANNED_PAGES": "Trang {pages} có vẻ là ảnh quét; văn bản ở đó có thể đọc chưa chính xác.",
    "LEGACY_ENCODING": (
        "Văn bản có dấu hiệu dùng bảng mã cũ (TCVN3, VNI); một số chữ có thể bị đọc sai."
    ),
    # Gạch nối dài là chủ ý, chép nguyên văn DESIGN.md §8.6.
    "OUT_OF_DOMAIN": "Bài có vẻ thuộc lĩnh vực khác CS–AI; kết quả có thể kém chính xác hơn.",  # noqa: RUF001
    "NO_REFERENCE_SECTION": (
        "Chưa tách được danh mục tài liệu tham khảo. Bạn có thể dán đoạn văn kèm danh mục ở "
        "“Kiểm tra bài mới”."
    ),
    "LLM_NOT_CONFIGURED": (
        "Chưa cấu hình mô hình ngôn ngữ, nên các nhận định chưa được đối chiếu; phần tra cứu "
        "tài liệu tham khảo vẫn chạy bình thường."
    ),
    "CLAIM_LIMIT_REACHED": (
        "Bài có nhiều nhận định hơn giới hạn của một lần kiểm tra; các nhận định vượt giới hạn "
        "chưa được đối chiếu."
    ),
    "COST_LIMIT_REACHED": (
        "Lần kiểm tra đã chạm hạn mức chi phí; các nhận định còn lại chưa được đối chiếu."
    ),
    "PDF_TEXT_FALLBACK": (
        "Tệp PDF được đọc bằng chế độ dự phòng; tên mục và vị trí trong bài có thể chưa chính xác."
    ),
}
GENERIC_WARNING = "Có cảnh báo khi xử lý."
HIDDEN_WARNINGS = frozenset({"SAMPLE_DOCUMENT"})


def warning_text(code: str, detail: Mapping[str, Any]) -> str:
    template = WARNINGS.get(code)
    if template is None:
        return GENERIC_WARNING
    pages = detail.get("pages")
    page_text = ", ".join(str(p) for p in pages) if isinstance(pages, list) else "[chưa có]"
    return template.format(pages=page_text)


def source_name(code: str) -> str:
    return SOURCES.get(code, code)
