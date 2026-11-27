"""Hợp đồng connector metadata và phần dùng chung của ba connector (spec §7.3, `09` §4.6).

Mỗi connector gọi một host API cố định qua `httpx.AsyncClient` dùng chung của
`PipelineDeps` với timeout riêng. Ánh xạ lỗi giống nhau ở mọi nguồn: 404 → không có
(`None`/`[]`); 429, 5xx, hết thời gian, lỗi mạng → `RetryableError` (kèm `retry_after`
từ header `Retry-After`); mã lỗi HTTP khác hoặc thân phản hồi không đọc được →
`ExternalServiceError`. Thông điệp lỗi chỉ nêu nguồn và mã HTTP: không bao giờ chứa truy
vấn (sinh từ bài của người dùng), URL kèm query string hay nội dung phản hồi.

Bản ghi thiếu hoặc lạ trường bị bỏ qua (không phải lỗi): nguồn vẫn được tính là đã tra
thành công. Chỉ phong bì phản hồi hỏng mới là lỗi của nguồn.
"""

import email.utils
import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from citeproof.core.domain import ParsedReference
from citeproof.core.errors import ExternalServiceError, RetryableError
from citeproof.integrations.metadata.models import WorkAuthor, WorkRecord

SOURCES: tuple[str, ...] = ("crossref", "openalex", "semantic_scholar")
USER_AGENT = "CiteProof/0.1 (+citation checker)"
DEFAULT_TIMEOUT_S = 15.0
# Truy vấn tìm theo tài liệu tham khảo dùng tối đa chừng này tác giả đầu.
SEARCH_AUTHORS = 3

_SPACE = re.compile(r"\s+")
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
_ARXIV = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|^10\.48550/arxiv\.|^arxiv:|^)"
    r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?/\d{7})"
    r"(?:v\d+)?(?:\.pdf)?/?$",
    re.IGNORECASE,
)


class MetadataConnector(Protocol):
    """Một nguồn metadata (`09` §4.6). 404 → `None`/`[]`; bản ghi không có tiêu đề bị bỏ."""

    name: str

    async def search(self, ref: ParsedReference, limit: int = 5) -> list[WorkRecord]: ...

    async def search_text(
        self, query: str, *, year: int | None = None, limit: int = 5
    ) -> list[WorkRecord]: ...

    async def get_by_doi(self, doi: str) -> WorkRecord | None: ...


def user_agent(contact_email: str | None) -> str:
    """User-Agent chung, không mang tên người dùng; thêm `mailto:` khi người vận hành đặt email."""
    if contact_email:
        return f"{USER_AGENT}; mailto:{contact_email}"
    return USER_AGENT


def search_fields(ref: ParsedReference) -> dict[str, Any]:
    """Các trường của `ref` mà connector dùng để tìm; cũng là phần `args` của khóa cache.

    Nhãn, URL, DOI không ảnh hưởng truy vấn nên không nằm trong khóa: cùng một tài liệu
    tham khảo ở hai bài khác nhau dùng lại được cache.
    """
    return {
        "title": ref.title,
        "authors": list(ref.authors[:SEARCH_AUTHORS]),
        "year": ref.year,
        "venue": ref.venue,
    }


def bibliographic_query(fields: Mapping[str, Any]) -> str:
    """Chuỗi thư mục "tiêu đề tác giả năm nơi đăng" cho tìm kiếm kiểu trích dẫn."""
    parts: list[str] = []
    if fields.get("title"):
        parts.append(str(fields["title"]))
    parts.extend(str(author) for author in fields.get("authors") or [] if author)
    if fields.get("year"):
        parts.append(str(fields["year"]))
    if fields.get("venue"):
        parts.append(str(fields["venue"]))
    return collapse(" ".join(parts))


def title_query(fields: Mapping[str, Any]) -> str:
    """Tiêu đề làm truy vấn; không có tiêu đề thì dùng chuỗi thư mục."""
    title = fields.get("title")
    return collapse(str(title)) if title else bibliographic_query(fields)


def year_window(year: int) -> tuple[int, int]:
    """Lọc năm ±1 cho `search_text` (công cụ `meta_search` của A1, `07a` §4.6.2)."""
    return year - 1, year + 1


def doi_path(doi: str) -> str:
    """DOI trong đường dẫn URL: giữ `/`, mã hóa ký tự còn lại (`?`, `#`, `;`, ngoặc…)."""
    return quote(doi.strip(), safe="/")


# --- HTTP -------------------------------------------------------------------------------------


async def get_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    timeout_s: float,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any | None:
    """GET một URL API cố định; `None` khi 404. Lỗi theo ánh xạ ở đầu module."""
    try:
        response = await http.get(
            url,
            params=dict(params) if params else None,
            headers=dict(headers) if headers else None,
            timeout=httpx.Timeout(timeout_s),
        )
    except httpx.TransportError as exc:
        # Gồm hết thời gian, lỗi kết nối và kết nối bị ngắt giữa chừng.
        raise RetryableError(
            f"Nguồn metadata {source} tạm thời không truy cập được.",
            code="METADATA_UNAVAILABLE",
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalServiceError(
            f"Nguồn metadata {source} trả phản hồi không đọc được.",
            code="METADATA_BAD_RESPONSE",
        ) from exc
    status = response.status_code
    if status == httpx.codes.NOT_FOUND:
        return None
    if status == httpx.codes.TOO_MANY_REQUESTS or status >= 500:
        raise RetryableError(
            f"Nguồn metadata {source} tạm thời không khả dụng (HTTP {status}).",
            code="METADATA_UNAVAILABLE",
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
        )
    if not httpx.codes.is_success(status):
        raise ExternalServiceError(
            f"Nguồn metadata {source} trả lỗi HTTP {status}.", code="METADATA_HTTP_ERROR"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ExternalServiceError(
            f"Nguồn metadata {source} trả phản hồi không phải JSON.",
            code="METADATA_BAD_RESPONSE",
        ) from exc


def bad_envelope(source: str) -> ExternalServiceError:
    """Phong bì phản hồi không đúng dạng (khác với một bản ghi hỏng, vốn chỉ bị bỏ qua)."""
    return ExternalServiceError(
        f"Nguồn metadata {source} trả phản hồi sai định dạng.", code="METADATA_BAD_RESPONSE"
    )


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Số giây chờ từ header `Retry-After` (số giây hoặc ngày HTTP); không đọc được → None."""
    if not value:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


# --- Đọc bản ghi phòng thủ --------------------------------------------------------------------


def parse_records(
    items: Iterable[Any], parse: Callable[[Any], WorkRecord | None], *, source: str = "metadata"
) -> list[WorkRecord]:
    """Dựng bản ghi từ từng mục; mục hỏng (sai kiểu, thiếu tiêu đề…) bị bỏ, không làm lỗi nguồn.

    Ngoại lệ: nguồn trả ≥ 1 mục mà mục nào cũng hỏng → `ExternalServiceError` (gateway ghi
    `error`, không cache) thay vì trả danh sách rỗng bị hiểu nhầm là `not_found`. Thông báo
    không chứa nội dung phản hồi.
    """
    records: list[WorkRecord] = []
    total = 0
    for item in items:
        total += 1
        record = parse_record(item, parse)
        if record is not None:
            records.append(record)
    if total and not records:
        raise ExternalServiceError(
            f"Nguồn metadata {source} trả {total} mục nhưng không đọc được mục nào.",
            code="METADATA_ALL_RECORDS_DROPPED",
        )
    return records


def parse_record(item: Any, parse: Callable[[Any], WorkRecord | None]) -> WorkRecord | None:
    if not isinstance(item, dict):
        return None
    try:
        return parse(item)
    except (TypeError, ValueError):
        # Giá trị lạ lọt qua các hàm `as_*` (gồm `ValidationError` của pydantic): bỏ bản ghi.
        return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_str(value: Any) -> str | None:
    """Chuỗi NFC đã gộp khoảng trắng; không phải chuỗi hoặc rỗng → None."""
    if not isinstance(value, str):
        return None
    text = collapse(value)
    return text or None


def first_str(value: Any) -> str | None:
    """Chuỗi khác rỗng đầu tiên của một danh sách (kiểu `title` của Crossref)."""
    for item in as_list(value):
        text = as_str(item)
        if text:
            return text
    return None


def as_year(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1000 <= value <= 2999 else None


def collapse(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def author_from_name(name: Any) -> WorkAuthor | None:
    """Tác giả từ tên hiển thị một chuỗi: "Họ, Tên" hoặc "Tên Họ" (họ là từ cuối)."""
    text = as_str(name)
    if text is None:
        return None
    if "," in text:
        family, _, given = text.partition(",")
        if family.strip():
            return WorkAuthor(family=family.strip(), given=given.strip() or None)
    tokens = text.split(" ")
    # Hậu tố ("Jr.", "III") không phải họ: "Martin Luther King Jr." có họ "King".
    if len(tokens) > 2 and tokens[-1].rstrip(".").casefold() in _NAME_SUFFIXES:
        tokens = tokens[:-1]
    return WorkAuthor(family=tokens[-1], given=" ".join(tokens[:-1]) or None)


def arxiv_id(value: Any) -> str | None:
    """arXiv ID không phiên bản từ ID, URL arXiv hoặc DOI `10.48550/arXiv.<id>`."""
    text = as_str(value)
    if text is None:
        return None
    match = _ARXIV.search(text)
    return match.group("id") if match else None
