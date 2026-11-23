from typing import Protocol

from citeproof.llm.types import ProviderRequest, ProviderResponse


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...
