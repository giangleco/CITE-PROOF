"""Tiền kiểm tất định của S8, không gọi LLM (spec §7.8).

Thứ tự: không phải claim → link NULL → reference chưa tìm thấy (không có tệp tải lên) →
tra cứu lỗi → không có nguồn → toàn văn không có chunk → ngôn ngữ nguồn không hỗ trợ → không
có LLM. V0 bỏ các bước về nguồn; V1 chỉ xét nguồn abstract; tệp tải lên của chính người dùng bỏ
qua trạng thái tài liệu tham khảo.
"""

from dataclasses import dataclass

from citeproof.core.enums import ReferenceStatus, UnverifiableReason, Verifier
from citeproof.modules.retrieval.pairs import ClaimPair, SourceChoice, SourceRef

__all__ = ["PairState", "chosen_source", "precheck"]

UNSUPPORTED_LANGUAGE = "other"


@dataclass(frozen=True)
class PairState:
    pair: ClaimPair
    # `reference_checks.status` của lần chạy, hoặc của lần chạy cha khi lần chạy này không có.
    reference_status: ReferenceStatus | None
    has_upload: bool
    sources: SourceChoice


def chosen_source(sources: SourceChoice, variant: Verifier) -> SourceRef | None:
    """Nguồn W5 sẽ đọc: V1 chỉ abstract; biến thể khác toàn văn rồi abstract; V0 không có."""
    if variant is Verifier.V0:
        return None
    if variant is Verifier.V1:
        return sources.abstract
    return sources.full_text or sources.abstract


def precheck(
    state: PairState, *, variant: Verifier, llm_configured: bool
) -> UnverifiableReason | None:
    pair = state.pair
    if not pair.is_claim:
        return UnverifiableReason.NOT_A_CLAIM
    if pair.reference_entry_id is None:
        return UnverifiableReason.REFERENCE_UNRESOLVED
    if not state.has_upload:
        if state.reference_status is ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES:
            return UnverifiableReason.REFERENCE_NOT_FOUND
        if state.reference_status is ReferenceStatus.LOOKUP_FAILED:
            return UnverifiableReason.PROCESSING_ERROR
    if variant is not Verifier.V0:
        source = chosen_source(state.sources, variant)
        if source is None:
            return UnverifiableReason.NO_ACCESSIBLE_TEXT
        if source.chunk_count == 0:
            return UnverifiableReason.SOURCE_PARSE_FAILED
        if source.language == UNSUPPORTED_LANGUAGE:
            return UnverifiableReason.SOURCE_LANGUAGE_UNSUPPORTED
    if not llm_configured:
        return UnverifiableReason.PROCESSING_ERROR
    return None
