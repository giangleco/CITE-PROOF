"""V1 — W5 trên mọi chunk của nguồn abstract."""

from citeproof.core.enums import Verifier
from citeproof.modules.verification.verifiers.base import (
    LLMVerifier,
    VerificationContext,
    VerificationOutcome,
)

__all__ = ["AbstractVerifier"]


class AbstractVerifier(LLMVerifier):
    variant = Verifier.V1

    async def verify(self, vc: VerificationContext) -> list[VerificationOutcome]:
        return [await self._on_source(vc, vc.sources.abstract)]
