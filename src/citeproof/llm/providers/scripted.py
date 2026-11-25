"""Provider kịch bản cho test và replay; không bao giờ gọi mạng."""

from collections.abc import Sequence

from citeproof.llm.types import ProviderRequest, ProviderResponse, TurnRequest, TurnResponse


class ScriptedProvider:
    """`responses` trả lần lượt cho `complete` (worker), `turns` cho `complete_turn` (agent)."""

    name = "scripted"

    def __init__(
        self,
        responses: Sequence[ProviderResponse | BaseException] = (),
        *,
        turns: Sequence[TurnResponse | BaseException] = (),
    ) -> None:
        self._responses = list(responses)
        self._turns = list(turns)
        self.requests: list[ProviderRequest] = []
        self.turn_requests: list[TurnRequest] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedProvider hết kịch bản")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def complete_turn(self, request: TurnRequest) -> TurnResponse:
        self.turn_requests.append(request)
        if not self._turns:
            raise AssertionError("ScriptedProvider hết kịch bản lượt agent")
        item = self._turns.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
