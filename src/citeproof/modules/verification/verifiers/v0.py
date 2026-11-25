"""V0 — W5 closed-book: chỉ claim và metadata reference, không có văn bản nguồn (QĐ-26)."""

from citeproof.core.enums import Verifier
from citeproof.modules.verification.verifiers.base import (
    LLMVerifier,
    VerificationContext,
    VerificationOutcome,
)

__all__ = ["ClosedBookVerifier"]


class ClosedBookVerifier(LLMVerifier):
    variant = Verifier.V0

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]:
        return [await self._judge(vc, source=None, chunks=())]
