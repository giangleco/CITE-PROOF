"""View model của báo cáo một lần chạy (spec web UI §7.5)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from citeproof.core.enums import (
    ClaimStatus,
    ContentLevelUsed,
    EvidenceRelation,
    FeedbackVerdict,
    ReferenceStatus,
    RefType,
    RunStatus,
    TitleMatch,
    UnverifiableReason,
    Verifier,
)


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class WarningView(_View):
    code: str
    detail: dict[str, Any] = {}


class RunHeader(_View):
    run_id: UUID
    document_id: UUID
    document_name: str
    is_sample: bool
    status: RunStatus
    created_at: datetime
    finished_at: datetime | None
    warnings: list[WarningView]
    # Biến thể cuối của lần chạy: báo cáo chỉ hiện dòng của verifier này (QĐ-28).
    verifier: Verifier
    # Provider LLM đóng băng vào lần chạy (spec §5.4); None: không gắn provider.
    provider: str | None = None


class Tally(_View):
    key: str
    count: int


TodoKind = Literal["flagged", "mismatch", "missing", "not_found", "lookup_failed"]


class TodoItem(_View):
    kind: TodoKind
    title: str
    detail: str
    href: str


class Overview(_View):
    header: RunHeader
    claim_tally: list[Tally]
    ref_tally: list[Tally]
    todos: list[TodoItem]
    is_empty: bool
    missing_count: int


class MismatchView(_View):
    field: str
    in_document: str | None
    found: str | None


class ReferenceView(_View):
    reference_check_id: UUID
    reference_entry_id: UUID
    index: int
    raw_text: str
    ref_type: RefType
    status: ReferenceStatus
    mismatches: list[MismatchView]
    sources_searched: list[str]
    sources_failed: list[str]
    doi: str | None
    url: str | None
    needs_user_review: bool
    # Số trích dẫn trong bài trỏ tới tài liệu này; 0 → "không được trích lần nào" (FR-21).
    cited_count: int


class ClaimRow(_View):
    id: UUID
    sentence: str
    status: ClaimStatus
    needs_user_review: bool
    page: int | None
    # None: nhận định `REFERENCE_UNRESOLVED`, chưa trỏ được tới tài liệu nào.
    ref_index: int | None


class EvidenceView(_View):
    relation: EvidenceRelation
    quote: str
    page: int | None
    section: str | None
    language: str
    url: str | None
    doi: str | None


class ClaimSheet(_View):
    id: UUID
    sentence: str
    status: ClaimStatus
    needs_user_review: bool
    page: int | None
    ref_index: int | None
    span: str
    marker: str
    section: str | None
    ref_raw: str | None
    unverifiable_reason: UnverifiableReason | None
    explanation: str
    limitations: str | None
    content_level_used: ContentLevelUsed
    evidence: list[EvidenceView]
    model: str
    prompt_version: str
    verifier: str
    my_feedback: FeedbackVerdict | None
    my_comment: str | None


class UploadedView(_View):
    title_match: TitleMatch
    source_file_id: UUID
    # Tải lên sau khi lần chạy được tạo: lần chạy chưa dùng tệp này (kiểm chứng lại, T-505).
    is_new: bool = False


class MissingSource(_View):
    reference_entry_id: UUID
    ref_index: int
    raw_text: str
    waiting_claims: int
    # `NO_ACCESSIBLE_TEXT` hoặc `REFERENCE_NOT_FOUND` (QĐ-31).
    reason: UnverifiableReason
    uploaded: UploadedView | None = None
