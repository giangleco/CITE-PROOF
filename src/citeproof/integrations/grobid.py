"""Client GROBID (ADR-004, spec §7.1, QĐ-03/QĐ-04/QĐ-22).

Gọi `processFulltextDocument` (TEI có tọa độ) và `processCitationList` qua
`httpx.AsyncClient` dùng chung của `PipelineDeps` với timeout riêng. Trả TEI dạng
chuỗi; việc parse TEI nằm ở `citeproof.parsing.tei` (module này chỉ phụ thuộc `core`).

Ánh xạ lỗi: không kết nối được, hết thời gian hoặc 503 (GROBID quá tải) →
`RetryableError` để job runner thử lại; mã khác 200 (lỗi xử lý chính tài liệu) →
`ParseError` để bên gọi dùng dự phòng ngay. Thông điệp lỗi không chứa nội dung phản hồi.
"""

from collections.abc import Mapping, Sequence

import httpx

from citeproof.core.errors import ParseError, RetryableError

FULLTEXT_FIELDS: Mapping[str, str | list[str]] = {
    "segmentSentences": "1",
    "teiCoordinates": ["p", "s", "head", "ref", "biblStruct", "figure"],
    "includeRawCitations": "1",
    "consolidateHeader": "0",
    "consolidateCitations": "0",
}
CITATION_FIELDS: Mapping[str, str] = {"includeRawCitations": "1", "consolidateCitations": "0"}
# Kết nối tới GROBID trong cùng mạng nội bộ: không cần chờ lâu như lúc xử lý.
_CONNECT_TIMEOUT_S = 10.0
_UNAVAILABLE = "Dịch vụ GROBID tạm thời không truy cập được."


class GrobidClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient, *, timeout_s: float) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._http = http
        self._timeout_s = timeout_s
        self._timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, _CONNECT_TIMEOUT_S))

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    async def process_fulltext(self, pdf: bytes) -> str:
        """TEI toàn văn của một PDF (câu tách sẵn, tọa độ cho p/s/head/ref/biblStruct/figure)."""
        return await self._post(
            "processFulltextDocument",
            data=FULLTEXT_FIELDS,
            files={"input": ("document.pdf", pdf, "application/pdf")},
        )

    async def process_citations(self, raw: Sequence[str]) -> str:
        """TEI `listBibl` cho các chuỗi tài liệu tham khảo thô, cùng thứ tự đầu vào."""
        data: dict[str, str | list[str]] = {**CITATION_FIELDS, "citations": [*raw]}
        return await self._post("processCitationList", data=data)

    async def _post(
        self,
        endpoint: str,
        *,
        data: Mapping[str, str | list[str]],
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> str:
        try:
            response = await self._http.post(
                f"{self._base_url}/api/{endpoint}",
                data=dict(data),
                files=files,
                headers={"Accept": "application/xml"},
                timeout=self._timeout,
            )
        except httpx.TransportError as exc:
            # Gồm lỗi kết nối, hết thời gian và kết nối bị ngắt giữa chừng (GROBID khởi động lại).
            raise RetryableError(_UNAVAILABLE, code="GROBID_UNAVAILABLE") from exc
        if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
            raise RetryableError(_UNAVAILABLE, code="GROBID_UNAVAILABLE")
        if response.status_code != httpx.codes.OK:
            raise ParseError(
                f"GROBID không xử lý được tài liệu (HTTP {response.status_code}).",
                code="GROBID_FAILED",
            )
        return response.text
