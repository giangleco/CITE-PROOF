from typing import Protocol

from citeproof.llm.types import ProviderRequest, ProviderResponse, TurnRequest, TurnResponse


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...

    async def complete_turn(self, request: TurnRequest) -> TurnResponse:
        """Một lượt agent kèm công cụ (AgentRunner); không ép `tool_choice`."""
        ...
