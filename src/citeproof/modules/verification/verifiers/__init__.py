"""Verifier của S8 theo biến thể (spec §7.8, §9)."""

from citeproof.core.enums import Verifier
from citeproof.core.errors import InternalError
from citeproof.modules.verification.verifiers.base import (
    PairVerifier,
    VerificationContext,
    VerificationOutcome,
)
from citeproof.modules.verification.verifiers.escalation import EscalatingVerifier
from citeproof.modules.verification.verifiers.v0 import ClosedBookVerifier
from citeproof.modules.verification.verifiers.v1 import AbstractVerifier
from citeproof.modules.verification.verifiers.v2 import RagVerifier
from citeproof.modules.verification.verifiers.v3 import MultiStepVerifier
from citeproof.modules.verification.verifiers.v4_agentic import AgenticVerifier
from citeproof.pipeline.deps import PipelineDeps

__all__ = [
    "AbstractVerifier",
    "AgenticVerifier",
    "ClosedBookVerifier",
    "EscalatingVerifier",
    "MultiStepVerifier",
    "PairVerifier",
    "RagVerifier",
    "VerificationContext",
    "VerificationOutcome",
    "make_verifier",
]


def make_verifier(variant: Verifier, deps: PipelineDeps) -> PairVerifier:
    if variant is Verifier.V0:
        return ClosedBookVerifier(deps)
    if variant is Verifier.V1:
        return AbstractVerifier(deps)
    if variant is Verifier.V2:
        return RagVerifier(deps)
    if variant is Verifier.V3:
        return MultiStepVerifier(deps)
    if variant is Verifier.V4:
        return AgenticVerifier(deps)
    if variant is Verifier.V2_A2:
        return EscalatingVerifier(deps)
    raise InternalError(f"Biến thể {variant.value} chưa có verifier")
