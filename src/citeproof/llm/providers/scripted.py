"""Provider kịch bản cho test và replay; không bao giờ gọi mạng."""

from collections.abc import Sequence

from citeproof.llm.types import ProviderRequest, ProviderResponse


class ScriptedProvider:
    name = "scripted"

    def __init__(self, responses: Sequence[ProviderResponse | BaseException]) -> None:
        self._responses = list(responses)
        self.requests: list[ProviderRequest] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedProvider hết kịch bản")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
