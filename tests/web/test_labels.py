from pathlib import Path

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
from citeproof.web import labels


def test_every_enum_member_labelled() -> None:
    assert set(labels.CLAIM_STATUS) == set(ClaimStatus)
    assert set(labels.REF_STATUS) == set(ReferenceStatus)
    assert set(labels.REASONS) == set(UnverifiableReason)
    assert set(labels.RELATIONS) == set(EvidenceRelation)
    assert set(labels.CONTENT_LEVEL) == set(ContentLevelUsed)
    assert set(labels.RUN_STATUS) == set(RunStatus)
    assert set(labels.REF_TYPES) == set(RefType)
    assert set(labels.STAGES) == set(StageName)
    assert set(labels.FEEDBACK) == set(FeedbackVerdict)


def test_labels_match_design_md() -> None:
    assert labels.CLAIM_STATUS[ClaimStatus.CONTRADICTED].label == "Mâu thuẫn với nguồn"
    assert labels.CLAIM_STATUS[ClaimStatus.CONTRADICTED].tone == "contra"
    assert labels.REF_STATUS[ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES].tone == "absent"
    assert labels.REF_STATUS[ReferenceStatus.LOOKUP_FAILED].tone == "syserr"
    assert labels.REF_STATUS[ReferenceStatus.LOOKUP_FAILED].label == "Chưa tra được, lỗi kết nối"
    assert labels.STAGES[StageName.S8][0] == "Đối chiếu nhận định"
    assert labels.RELATIONS[EvidenceRelation.RELATED] == "Đoạn liên quan gần nhất"


def test_only_contradicted_is_red_among_statuses() -> None:
    reds = [k for k, b in labels.CLAIM_STATUS.items() if b.tone == "contra"]
    reds += [k for k, b in labels.REF_STATUS.items() if b.tone == "contra"]
    assert reds == [ClaimStatus.CONTRADICTED]


def test_no_forbidden_words_in_labels() -> None:
    texts: list[str] = []
    for table in (labels.CLAIM_STATUS, labels.REF_STATUS, labels.RUN_STATUS):
        texts += [b.label for b in table.values()]
    for table in (labels.REASONS, labels.RELATIONS, labels.CONTENT_LEVEL, labels.REF_TYPES):
        texts += list(table.values())
    texts += list(labels.WARNINGS.values()) + [s for pair in labels.STAGES.values() for s in pair]
    for text in texts:
        for word in labels.FORBIDDEN_WORDS:
            assert word not in text.lower(), text


def test_warning_text() -> None:
    assert labels.warning_text("XYZ", {}) == "Có cảnh báo khi xử lý."
    assert labels.warning_text("SCANNED_PAGES", {"pages": [41, 42, 43]}).startswith(
        "Trang 41, 42, 43 có vẻ là ảnh quét"
    )


# Nguyên văn spec lõi §13 "Mã cảnh báo mới".
NEW_WARNINGS = {
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
    # Quyết định của controller (Task 7, fix round 1): S2 tách bằng rule vì GROBID/W1 lỗi.
    "REFERENCE_PARSE_DEGRADED": (
        "Một số tài liệu tham khảo được tách bằng luật đơn giản vì dịch vụ phân tích tạm thời "
        "không khả dụng; thông tin có thể chưa đầy đủ."
    ),
}
DESIGN_MD = Path(__file__).resolve().parents[2] / ".stitch" / "DESIGN.md"


def test_new_warning_labels() -> None:
    for code, text in NEW_WARNINGS.items():
        assert labels.WARNINGS[code] == text
        assert labels.warning_text(code, {}) == text
        assert not any(word in text.lower() for word in labels.FORBIDDEN_WORDS), code


def test_new_warning_rows_in_design_md() -> None:
    design = DESIGN_MD.read_text(encoding="utf-8")
    for code, text in NEW_WARNINGS.items():
        assert f"| `{code}` | {text} |" in design, code
