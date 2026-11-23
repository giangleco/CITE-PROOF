"""Enum miền dữ liệu; giá trị khớp 08 §4.2, 04 §4.5 và 07a §4.7.6."""

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class DocumentKind(StrEnum):
    FILE = "FILE"
    TEXT = "TEXT"


class Language(StrEnum):
    VI = "vi"
    EN = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class DomainFlag(StrEnum):
    IN_SCOPE = "in_scope"
    OUT_OF_DOMAIN = "out_of_domain"
    UNKNOWN = "unknown"


class RefType(StrEnum):
    ARTICLE = "ARTICLE"
    CONFERENCE = "CONFERENCE"
    PREPRINT = "PREPRINT"
    BOOK = "BOOK"
    THESIS = "THESIS"
    WEB = "WEB"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ParseMethod(StrEnum):
    GROBID = "grobid"
    RULE = "rule"
    LLM = "llm"


class ReferenceStatus(StrEnum):
    MATCHED = "MATCHED"
    METADATA_MISMATCH = "METADATA_MISMATCH"
    NOT_FOUND_IN_SEARCHED_SOURCES = "NOT_FOUND_IN_SEARCHED_SOURCES"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    PARSE_FAILED = "PARSE_FAILED"
    LOOKUP_FAILED = "LOOKUP_FAILED"


class ResolvedBy(StrEnum):
    CASCADE = "cascade"
    AGENT = "agent"


class SourceOrigin(StrEnum):
    OPEN_ACCESS = "OPEN_ACCESS"
    USER_UPLOAD = "USER_UPLOAD"


class ContentLevel(StrEnum):
    ABSTRACT = "ABSTRACT"
    FULL_TEXT = "FULL_TEXT"


class ContentLevelUsed(StrEnum):
    NONE = "NONE"
    ABSTRACT = "ABSTRACT"
    FULL_TEXT = "FULL_TEXT"


class TitleMatch(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class ChunkRegion(StrEnum):
    BODY = "BODY"
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    OTHER = "OTHER"


class LinkMethod(StrEnum):
    NUMERIC = "numeric"
    AUTHOR_YEAR = "author_year"
    LLM = "llm"


class RunStatus(StrEnum):
    """Theo 09 §4.4."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageName(StrEnum):
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    S6 = "S6"
    S7 = "S7"
    S8 = "S8"
    S9 = "S9"


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class UnverifiableReason(StrEnum):
    REFERENCE_UNRESOLVED = "REFERENCE_UNRESOLVED"
    REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
    NO_ACCESSIBLE_TEXT = "NO_ACCESSIBLE_TEXT"
    ABSTRACT_ONLY_INSUFFICIENT = "ABSTRACT_ONLY_INSUFFICIENT"
    SOURCE_PARSE_FAILED = "SOURCE_PARSE_FAILED"
    EVIDENCE_INCONCLUSIVE = "EVIDENCE_INCONCLUSIVE"
    SOURCE_LANGUAGE_UNSUPPORTED = "SOURCE_LANGUAGE_UNSUPPORTED"
    NOT_A_CLAIM = "NOT_A_CLAIM"
    PROCESSING_ERROR = "PROCESSING_ERROR"


class EvidenceRelation(StrEnum):
    SUPPORTS = "SUPPORTS"
    PARTIALLY_SUPPORTS = "PARTIALLY_SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    RELATED = "RELATED"


class FeedbackTarget(StrEnum):
    CLAIM_VERIFICATION = "CLAIM_VERIFICATION"
    REFERENCE_CHECK = "REFERENCE_CHECK"


class FeedbackVerdict(StrEnum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    UNSURE = "UNSURE"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class AgentName(StrEnum):
    REFERENCE_RESOLVER = "reference_resolver"
    EVIDENCE_INVESTIGATOR = "evidence_investigator"


class AgentTargetType(StrEnum):
    REFERENCE_ENTRY = "REFERENCE_ENTRY"
    CLAIM = "CLAIM"


class AgentRunStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    NO_SUBMIT = "NO_SUBMIT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    ERROR = "ERROR"


class Verifier(StrEnum):
    V0 = "V0"
    V1 = "V1"
    V2 = "V2"
    V3 = "V3"
    V4 = "V4"
    V2_A2 = "V2+A2"


class MetadataOutcome(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ERROR = "error"
