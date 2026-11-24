"""DAG giai đoạn S1..S9, giao thức stage và registry (07a §4.3.1)."""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from citeproof.core.enums import StageName
from citeproof.pipeline.jobs import ClaimedJob

STAGE_DEPENDENCIES: Mapping[StageName, frozenset[StageName]] = MappingProxyType(
    {
        StageName.S1: frozenset(),
        StageName.S2: frozenset({StageName.S1}),
        StageName.S3: frozenset({StageName.S2}),
        StageName.S4: frozenset({StageName.S2}),
        StageName.S5: frozenset({StageName.S3, StageName.S4}),
        StageName.S6: frozenset({StageName.S5}),
        StageName.S7: frozenset({StageName.S6}),
        StageName.S8: frozenset({StageName.S7}),
        StageName.S9: frozenset({StageName.S8}),
    }
)


@dataclass(frozen=True)
class StageResult:
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageContext:
    """Thông tin một lần chạy stage: lần chạy, lease đang giữ và giới hạn lượt thử.

    Stage ghi kết quả theo mục qua `fenced_session(session_factory, ctx.lease)`.
    """

    run_id: UUID
    lease: ClaimedJob
    max_attempts: int

    @property
    def attempt(self) -> int:
        return self.lease.attempts

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts


class PipelineStage(Protocol):
    name: StageName

    async def run(self, ctx: StageContext) -> StageResult: ...


class NoopStage:
    """Stage không làm gì; dùng khi các module nghiệp vụ chưa có."""

    def __init__(self, name: StageName) -> None:
        self.name = name

    async def run(self, ctx: StageContext) -> StageResult:
        return StageResult()


class StageRegistry:
    def __init__(self, stages: Iterable[PipelineStage]) -> None:
        stage_list = list(stages)
        counts = Counter(stage.name for stage in stage_list)
        if set(counts) != set(StageName) or any(count != 1 for count in counts.values()):
            raise ValueError("registry phải có đủ chín stage, mỗi stage đúng một lần")
        self._stages: dict[StageName, PipelineStage] = {stage.name: stage for stage in stage_list}

    def get(self, name: StageName) -> PipelineStage:
        return self._stages[name]

    @classmethod
    def noop(cls) -> "StageRegistry":
        return cls(NoopStage(name) for name in StageName)
