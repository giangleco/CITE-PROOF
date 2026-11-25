"""Schema `verdict/2` do W5 và A2 trả về (spec §7.8, §8.2)."""

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from citeproof.core.enums import ClaimStatus, EvidenceRelation
from citeproof.llm.types import OutputModel

__all__ = ["Verdict", "VerdictEvidence"]


class VerdictEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    quote: str = Field(min_length=1)
    relation: EvidenceRelation


class Verdict(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "verdict/2"

    schema_version: Literal["verdict/2"] = "verdict/2"
    status: ClaimStatus
    evidence: list[VerdictEvidence]
    abstain_cause: Literal["TEXT_UNREADABLE", "CONTEXT_INCONCLUSIVE"] | None = None
    explanation: str
    limitations: str | None = None

    @model_validator(mode="after")
    def _abstain_only_when_unverifiable(self) -> Self:
        if self.abstain_cause is not None and self.status is not ClaimStatus.UNVERIFIABLE:
            raise ValueError("abstain_cause chỉ đi với trạng thái UNVERIFIABLE")
        return self
