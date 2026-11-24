"""Helper cho test pipeline: dựng PipelineDeps, chạy cạn hàng đợi, các stage giả lập."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import StageName
from citeproof.core.settings import Settings
from citeproof.integrations.http_safe import Resolver, SafeHttpClient
from citeproof.integrations.metadata import make_gateway_factory
from citeproof.llm.cache import PgCacheStore
from citeproof.llm.calls import PgCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.providers.base import LLMProvider
from citeproof.pipeline.deps import PROMPTS_ROOT, PipelineDeps, make_grobid_client
from citeproof.pipeline.jobs import ClaimedJob
from citeproof.pipeline.stages import StageContext, StageRegistry, StageResult
from citeproof.pipeline.worker import work_once
from citeproof.storage import LocalStorage

PUBLIC_IP = "93.184.216.34"


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
    `GrobidClient` (tới `settings.grobid_url`) và gateway metadata. Không truyền `transport`
    thì `grobid` là None (S1 dùng pypdfium2), để test không bao giờ gọi GROBID thật. Test có
    tra cứu metadata (S3) phải truyền `transport`. Gateway dùng TTL cache của `settings` và
    không chờ thật (limiter, thử lại).
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
    http = httpx.AsyncClient(transport=transport)
    return PipelineDeps(
        session_factory=session_factory,
        storage=LocalStorage(settings.storage_dir),
        settings=settings,
        llm=llm,
        prompts=PromptLoader(PROMPTS_ROOT),
        http=http,
        safe_http=SafeHttpClient(resolver=resolver or public_resolver, transport=transport),
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
