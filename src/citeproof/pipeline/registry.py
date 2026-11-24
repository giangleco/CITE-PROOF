"""Ghép stage thật thành registry cho worker (spec §6.3)."""

from citeproof.core.enums import StageName
from citeproof.pipeline.demo import DemoStage
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.stages import NoopStage, PipelineStage, StageRegistry


def _real_stages(deps: PipelineDeps) -> dict[StageName, PipelineStage]:
    """Stage đã có cài đặt thật; task sau thêm vào đây (ví dụ S1: `ParseStage(deps)`)."""
    return {}


def build_registry(deps: PipelineDeps) -> StageRegistry:
    """Stage thật nếu đã có, ngược lại noop; ở môi trường local bọc từng stage bằng DemoStage."""
    real = _real_stages(deps)
    stages: list[PipelineStage] = [real.get(name) or NoopStage(name) for name in StageName]
    if deps.settings.env == "local":
        stages = [DemoStage(stage, deps.session_factory) for stage in stages]
    return StageRegistry(stages)
