"""Verifier của S8 theo biến thể (spec §7.8). V3 (Task 17), V4 và V2+A2 (Task 26) thêm sau."""

from citeproof.core.enums import Verifier
from citeproof.core.errors import InternalError
from citeproof.modules.verification.verifiers.base import (
    PairVerifier,
    VerificationContext,
    VerificationOutcome,
)
from citeproof.modules.verification.verifiers.v0 import ClosedBookVerifier
from citeproof.modules.verification.verifiers.v1 import AbstractVerifier
from citeproof.modules.verification.verifiers.v2 import RagVerifier
from citeproof.pipeline.deps import PipelineDeps

__all__ = [
    "AbstractVerifier",
    "ClosedBookVerifier",
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
    raise InternalError(f"Biến thể {variant.value} chưa có verifier")
