"""Bản mở theo OpenAlex `best_oa_location.pdf_url` (+ `license`) (spec §7.5, QĐ-07).

Tra theo DOI qua `MetadataGateway` (cache `metadata_cache`, giới hạn tốc độ, ngắt mạch): S3
thường đã tra OpenAlex theo DOI nên lần này trúng cache. Nguồn lỗi hoặc bị ngắt mạch → không
có lựa chọn nào (gateway không ném lỗi của nguồn).
"""

from collections.abc import Callable

from citeproof.integrations.metadata import MetadataGateway
from citeproof.integrations.oa.base import AccessOption, WorkRef

SOURCE = "openalex"


class OpenAlexAccess:
    def __init__(self, gateway_factory: Callable[[], MetadataGateway]) -> None:
        self._factory = gateway_factory
        self._gateway: MetadataGateway | None = None

    def _gateway_for_run(self) -> MetadataGateway:
        # Một gateway (một bộ ngắt mạch) cho cả đối tượng: S5 dựng resolver mới mỗi lần chạy.
        if self._gateway is None:
            self._gateway = self._factory()
        return self._gateway

    async def resolve(self, work: WorkRef) -> list[AccessOption]:
        if not work.doi:
            return []
        outcome = await self._gateway_for_run().by_doi(SOURCE, work.doi)
        if outcome.status != "ok":
            return []
        return [
            AccessOption(url=location.pdf_url, license=location.license, provider="openalex")
            for record in outcome.records[:1]
            for location in record.oa
        ]
