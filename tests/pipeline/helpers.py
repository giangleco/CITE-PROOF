"""Helper cho test pipeline: dựng PipelineDeps, chạy cạn hàng đợi, các stage giả lập."""

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import StageName
from citeproof.core.settings import Settings
from citeproof.integrations.http_safe import Resolver, SafeHttpClient
from citeproof.integrations.metadata import make_gateway_factory
from citeproof.integrations.oa import HostRateLimits
from citeproof.llm.cache import PgCacheStore
from citeproof.llm.calls import PgCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.types import ProviderRequest, ProviderResponse
from citeproof.modules.citations.llm_extract import ClaimExtractionBatch
from citeproof.modules.references.llm_parse import ParsedReferenceBatch
from citeproof.modules.retrieval.translate import TranslatedClaim
from citeproof.modules.verification.decompose import SubClaims
from citeproof.modules.verification.verdict import Verdict
from citeproof.pipeline.deps import (
    PROMPTS_ROOT,
    PipelineDeps,
    make_grobid_client,
    make_source_http,
)
from citeproof.pipeline.jobs import ClaimedJob
from citeproof.pipeline.stages import StageContext, StageRegistry, StageResult
from citeproof.pipeline.worker import work_once
from citeproof.storage import LocalStorage

PUBLIC_IP = "93.184.216.34"


class NetworkRefusedError(AssertionError):
    """Test gọi tới API thật qua `deps.http` mà không truyền `transport` cho `make_test_deps`."""


def refuse_network(request: httpx.Request) -> httpx.Response:
    """Handler mặc định của `deps.http` trong test: từ chối mọi request, báo rõ cách sửa."""
    raise NetworkRefusedError(
        f"make_test_deps không cho gọi mạng thật ({request.method} "
        f"{request.url.host}{request.url.path}); hãy truyền transport=httpx.MockTransport(...)"
    )


METADATA_HOSTS = frozenset({"api.crossref.org", "api.openalex.org", "api.semanticscholar.org"})


def metadata_not_found(request: httpx.Request) -> httpx.Response:
    """Handler cho test chạy cả pipeline: API metadata trả 404 cho mọi tra cứu (S3 ra "chưa
    tìm thấy"), host khác vẫn bị từ chối như `refuse_network` (đặt `grobid_url=""`).
    """
    if request.url.host in METADATA_HOSTS:
        return httpx.Response(404)
    return refuse_network(request)


async def no_sleep(seconds: float) -> None:
    """Không chờ: giới hạn tốc độ và thử lại của gateway metadata chạy tức thì trong test."""
    return None


async def public_resolver(host: str, port: int) -> list[str]:
    """Resolver giả: mọi host đều trỏ tới một IP công khai (không tra DNS thật)."""
    return [PUBLIC_IP]


def make_test_deps(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider: LLMProvider | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> PipelineDeps:
    """PipelineDeps cho test stage: không mạng thật, LLM chỉ khi truyền `provider`.

    `transport` (ví dụ `httpx.MockTransport`) dùng chung cho `http`, `safe_http`,
    `source_http`, `GrobidClient` (tới `settings.grobid_url`) và gateway metadata. Không truyền
    `transport` thì `grobid` là None (S1 dùng pypdfium2), còn `http` (đường gọi API metadata)
    và `source_http` (tải bản mở ở S5) từ chối mọi request bằng `NetworkRefusedError`, để test
    không bao giờ gọi GROBID, API hay máy chủ PDF thật. Gateway dùng TTL cache của `settings`
    và không chờ thật (limiter, thử lại).
    """
    llm = (
        LLMClient(
            provider,
            PgCacheStore(session_factory),
            PgCallLogger(session_factory),
            prices=settings.llm_prices,
        )
        if provider is not None
        else None
    )
    http = httpx.AsyncClient(transport=transport or httpx.MockTransport(refuse_network))
    return PipelineDeps(
        session_factory=session_factory,
        storage=LocalStorage(settings.storage_dir),
        settings=settings,
        llm=llm,
        prompts=PromptLoader(PROMPTS_ROOT),
        http=http,
        safe_http=SafeHttpClient(resolver=resolver or public_resolver, transport=transport),
        source_http=make_source_http(
            settings,
            resolver=resolver or public_resolver,
            transport=transport or httpx.MockTransport(refuse_network),
        ),
        source_limits=HostRateLimits(sleep=no_sleep),
        grobid=make_grobid_client(settings, http) if transport is not None else None,
        metadata=make_gateway_factory(settings, http, session_factory, sleep=no_sleep),
    )


async def drain(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    settings: Settings,
    worker_id: str = "w1",
) -> int:
    """Gọi `work_once` đến khi không còn job nhận được; trả về số job đã xử lý."""
    processed = 0
    while await work_once(session_factory, registry, worker_id, settings):
        processed += 1
    return processed


def stage_context(
    run_id: uuid.UUID,
    *,
    stage: StageName = StageName.S1,
    attempts: int = 1,
    max_attempts: int = 3,
) -> StageContext:
    """StageContext với lease giả (không có dòng job tương ứng), để gọi stage trực tiếp."""
    lease = ClaimedJob(
        id=uuid.uuid4(),
        run_id=run_id,
        stage=stage,
        attempts=attempts,
        payload={},
        locked_at=datetime.now(UTC),
    )
    return StageContext(run_id=run_id, lease=lease, max_attempts=max_attempts)


class CountingStage:
    """Stage ghi lại context của mỗi lần được gọi; trả về các cảnh báo cố định."""

    def __init__(self, name: StageName, warnings: Sequence[str] = ()) -> None:
        self.name = name
        self.warnings = tuple(warnings)
        self.calls: list[StageContext] = []

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls.append(ctx)
        return StageResult(warnings=self.warnings)


class FailingStage(CountingStage):
    """Ném `error` ở `fail_times` lần gọi đầu, sau đó chạy bình thường."""

    def __init__(self, name: StageName, fail_times: int, error: BaseException) -> None:
        super().__init__(name)
        self.fail_times = fail_times
        self.error = error

    async def run(self, ctx: StageContext) -> StageResult:
        result = await super().run(ctx)
        if len(self.calls) <= self.fail_times:
            raise self.error
        return result


def counting_registry(
    *overrides: CountingStage,
) -> tuple[StageRegistry, dict[StageName, CountingStage]]:
    """Registry gồm CountingStage cho mọi stage, thay bằng `overrides` theo tên."""
    stages = {name: CountingStage(name) for name in StageName}
    for stage in overrides:
        stages[stage.name] = stage
    return StageRegistry(stages.values()), stages


def pipeline_settings(
    *,
    job_max_attempts: int = 3,
    job_lock_timeout_seconds: int = 900,
    job_heartbeat_seconds: float = 30.0,
) -> Settings:
    """Settings cố định cho test pipeline (không phụ thuộc biến môi trường)."""
    return Settings(
        job_max_attempts=job_max_attempts,
        job_backoff_base_seconds=5.0,
        job_lock_timeout_seconds=job_lock_timeout_seconds,
        job_heartbeat_seconds=job_heartbeat_seconds,
        worker_poll_interval_seconds=0.01,
    )


# --- LLM giả theo tác vụ ----------------------------------------------------------------------

# Tác vụ worker nhận ra từ tiêu đề JSON Schema đầu ra (tên lớp Pydantic) của request.
LLM_TASK_BY_SCHEMA: Mapping[str, str] = {
    ParsedReferenceBatch.__name__: "W1",
    ClaimExtractionBatch.__name__: "W2",
    TranslatedClaim.__name__: "W3",
    SubClaims.__name__: "W4",
    Verdict.__name__: "W5",
}

RouteHandler = Callable[[ProviderRequest], ProviderResponse]


def json_response(payload: Mapping[str, Any], *, model: str = "") -> ProviderResponse:
    """Phản hồi provider hợp lệ chứa `payload` dạng JSON."""
    return ProviderResponse(
        text=json.dumps(payload, ensure_ascii=False),
        input_tokens=1000,
        output_tokens=200,
        stop_reason="end_turn",
        model=model,
    )


class RoutedProvider:
    """Provider giả trả lời theo tác vụ (W1…W5) thay vì theo thứ tự gọi.

    Stage chạy song song (S4, S7, S8 gọi LLM đồng thời) nên `ScriptedProvider` không giữ được
    thứ tự; ở đây mỗi tác vụ có một handler `request -> ProviderResponse` (hoặc danh sách phản
    hồi lấy lần lượt). Tác vụ không có kịch bản → `AssertionError`. `requests` ghi lại
    `(tác vụ, request)` theo thứ tự đến.
    """

    name = "routed"

    def __init__(
        self, routes: Mapping[str, RouteHandler | Sequence[ProviderResponse | BaseException]]
    ) -> None:
        self._handlers: dict[str, RouteHandler] = {}
        self._queues: dict[str, list[ProviderResponse | BaseException]] = {}
        for task, route in routes.items():
            if callable(route):
                self._handlers[task] = route
            else:
                self._queues[task] = list(route)
        self.requests: list[tuple[str, ProviderRequest]] = []

    @staticmethod
    def task_of(request: ProviderRequest) -> str:
        title = request.output_schema.get("title")
        task = LLM_TASK_BY_SCHEMA.get(title) if isinstance(title, str) else None
        if task is None:
            raise AssertionError(f"RoutedProvider: schema đầu ra lạ {title!r}")
        return task

    def calls(self, task: str) -> list[ProviderRequest]:
        return [request for name, request in self.requests if name == task]

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        task = self.task_of(request)
        self.requests.append((task, request))
        handler = self._handlers.get(task)
        if handler is not None:
            return handler(request)
        queue = self._queues.get(task)
        if not queue:
            raise AssertionError(f"RoutedProvider: hết kịch bản cho {task}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
