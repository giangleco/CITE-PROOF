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
    UnverifiableReason,
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
    finished_at: datetime | None
    warnings: list[WarningView]


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


class ClaimRow(_View):
    id: UUID
    sentence: str
    status: ClaimStatus
    needs_user_review: bool
    page: int | None
    ref_index: int


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
    ref_index: int
    span: str
    marker: str
    section: str | None
    ref_raw: str
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


class MissingSource(_View):
    ref_index: int
    raw_text: str
    waiting_claims: int
