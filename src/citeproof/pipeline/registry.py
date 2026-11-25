"""Ghép stage thật thành registry cho worker (spec §6.3)."""

from citeproof.core.enums import StageName
from citeproof.modules.citations.stage import CitationsStage
from citeproof.modules.documents.stage import ParseStage
from citeproof.modules.references.resolver_agent import AgentResolver
from citeproof.modules.references.stage_check import CheckReferencesStage
from citeproof.modules.references.stage_extract import ExtractReferencesStage
from citeproof.modules.reports.stage import FinalizeStage
from citeproof.modules.retrieval.stage import RetrieveStage
from citeproof.modules.sources.stage_fetch import FetchSourcesStage
from citeproof.modules.sources.stage_index import IndexSourcesStage
from citeproof.modules.verification.stage import VerifyStage
from citeproof.pipeline.demo import DemoStage
from citeproof.pipeline.deps import PipelineDeps
from citeproof.pipeline.stages import NoopStage, PipelineStage, StageRegistry


def _real_stages(deps: PipelineDeps) -> dict[StageName, PipelineStage]:
    """Stage đã có cài đặt thật; task sau thêm vào đây.

    S3 có bước leo thang A1 khi có `AgentRunner`; A1 chỉ chạy khi cấu hình lần chạy bật nó.
    """
    escalation = AgentResolver(deps) if deps.agents is not None else None
    return {
        StageName.S1: ParseStage(deps),
        StageName.S2: ExtractReferencesStage(deps),
        StageName.S3: CheckReferencesStage(deps, escalation=escalation),
        StageName.S4: CitationsStage(deps),
        StageName.S5: FetchSourcesStage(deps),
        StageName.S6: IndexSourcesStage(deps),
        StageName.S7: RetrieveStage(deps),
        StageName.S8: VerifyStage(deps),
        StageName.S9: FinalizeStage(deps),
    }


def build_registry(deps: PipelineDeps) -> StageRegistry:
    """Stage thật nếu đã có, ngược lại noop; ở môi trường local bọc từng stage bằng DemoStage."""
    real = _real_stages(deps)
    stages: list[PipelineStage] = [real.get(name) or NoopStage(name) for name in StageName]
    if deps.settings.env == "local":
        stages = [DemoStage(stage, deps.session_factory) for stage in stages]
    return StageRegistry(stages)
