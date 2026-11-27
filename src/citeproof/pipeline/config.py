"""Cấu hình lần chạy (`RunConfig`) và thông tin lần chạy cho stage (spec §6.2, QĐ-46).

`RunConfig` được "đóng băng" vào `verification_runs.config` khi tạo lần chạy
(`RunConfig.resolve`): model, effort, phiên bản prompt và bộ công cụ không đổi khi lần
chạy tiếp tục sau lỗi. Cấu hình cũ (chỉ có `{"variant": "V2"}`) đọc bằng
`RunConfig.load`, khóa thiếu lấy mặc định trong mã (`core.defaults`), không từ `Settings`.

`provider` là định danh provider LLM lúc tạo lần chạy (spec §5.4); None khi chưa cấu hình LLM
hoặc với cấu hình cũ không có khóa này. Lần chạy gắn provider không được tiếp tục (stage cần
LLM, lần chạy con) với provider khác (`ensure_same_provider`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core import defaults
from citeproof.core.enums import AgentName, LLMTask, StageName, Verifier
from citeproof.core.errors import ConflictError, NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import Document, VerificationRun
from citeproof.llm.identity import provider_identity
from citeproof.web import labels

PROMPT_VERSIONS: Mapping[str, str] = MappingProxyType({task.value: "v1" for task in LLMTask})
TOOL_CATALOGS: Mapping[str, str] = MappingProxyType({"meta": "v1", "source": "v1"})
# A1, A2 tắt mặc định (QĐ-47); ngân sách theo spec §6.2.
AGENT_DEFAULTS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        AgentName.REFERENCE_RESOLVER.value: MappingProxyType(
            {
                "max_steps": 6,
                "max_context_tokens": 10_000,
                "max_total_input_tokens": 40_000,
                "max_wall_seconds": 120.0,
            }
        ),
        AgentName.EVIDENCE_INVESTIGATOR.value: MappingProxyType(
            {
                "max_steps": 8,
                "max_context_tokens": 20_000,
                "max_total_input_tokens": 90_000,
                "max_wall_seconds": 180.0,
            }
        ),
    }
)
# Biến thể có A2: admin chọn chúng là bật A2 cho lần chạy (QĐ-47, preflight F31).
_A2_VARIANTS = frozenset({Verifier.V4, Verifier.V2_A2})
# Bảng theo khóa: khóa thiếu trong cấu hình đã lưu lấy giá trị mặc định tương ứng.
_TABLE_DEFAULTS: Mapping[str, Mapping[str, Any]] = {
    "models": defaults.LLM_MODELS,
    "effort": defaults.LLM_EFFORT,
    "max_tokens": defaults.LLM_MAX_TOKENS,
    "prompts": PROMPT_VERSIONS,
    "tool_catalogs": TOOL_CATALOGS,
}


@dataclass(frozen=True)
class TaskSpec:
    model: str
    effort: str | None
    max_tokens: int
    prompt_version: str


class AgentConfig(BaseModel):
    enabled: bool = False
    max_steps: int
    max_context_tokens: int
    max_total_input_tokens: int
    max_wall_seconds: float


class RunLimits(BaseModel):
    max_claims: int = defaults.RUN_MAX_CLAIMS
    max_cost_usd: float = defaults.RUN_MAX_COST_USD


class RunScope(BaseModel):
    """Phạm vi của lần chạy con (kiểm chứng lại một phần, QĐ-30)."""

    kind: Literal["upload", "lookup_retry"]
    reference_entry_ids: list[UUID]
    from_stage: StageName


def _default_agents() -> dict[str, AgentConfig]:
    return {name: AgentConfig(**values) for name, values in AGENT_DEFAULTS.items()}


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    variant: Verifier = Verifier.V2
    cross_lingual: Literal["X1", "X2"] = "X2"
    retrieval_k: int = defaults.RETRIEVAL_K
    agents: dict[str, AgentConfig] = Field(default_factory=_default_agents)
    models: dict[str, str] = Field(default_factory=lambda: dict(defaults.LLM_MODELS))
    effort: dict[str, str] = Field(default_factory=lambda: dict(defaults.LLM_EFFORT))
    max_tokens: dict[str, int] = Field(default_factory=lambda: dict(defaults.LLM_MAX_TOKENS))
    prompts: dict[str, str] = Field(default_factory=lambda: dict(PROMPT_VERSIONS))
    tool_catalogs: dict[str, str] = Field(default_factory=lambda: dict(TOOL_CATALOGS))
    limits: RunLimits = Field(default_factory=RunLimits)
    demo: dict[str, Any] | None = None
    scope: RunScope | None = None
    # Định danh provider LLM đóng băng (`llm.identity`); None: không gắn provider nào.
    provider: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _fill_partial_tables(cls, data: Any) -> Any:
        """Gộp bảng thiếu khóa (model theo tác vụ, cấu hình agent) với mặc định trong mã."""
        if not isinstance(data, Mapping):
            return data
        merged = dict(data)
        for key, table_defaults in _TABLE_DEFAULTS.items():
            value = merged.get(key)
            if isinstance(value, Mapping):
                merged[key] = {**table_defaults, **value}
        agents = merged.get("agents")
        if isinstance(agents, Mapping):
            filled: dict[str, Any] = {name: dict(cfg) for name, cfg in AGENT_DEFAULTS.items()}
            for name, cfg in agents.items():
                if isinstance(cfg, Mapping):
                    filled[name] = {**filled.get(name, {}), **cfg}
                else:
                    filled[name] = cfg
            merged["agents"] = filled
        return merged

    @classmethod
    def load(cls, raw: Mapping[str, Any] | None) -> Self:
        """Đọc cấu hình đã lưu của lần chạy; khóa thiếu lấy mặc định trong mã."""
        return cls.model_validate(dict(raw or {}))

    @classmethod
    def resolve(
        cls,
        settings: Settings,
        *,
        variant: Verifier,
        demo: Mapping[str, Any] | None = None,
        scope: RunScope | None = None,
    ) -> Self:
        """Cấu hình đóng băng cho lần chạy mới, lấy model, effort và giới hạn từ `settings`."""
        agents = {name: dict(values) for name, values in AGENT_DEFAULTS.items()}
        agents[AgentName.EVIDENCE_INVESTIGATOR.value]["enabled"] = variant in _A2_VARIANTS
        return cls.model_validate(
            {
                "variant": variant,
                "retrieval_k": settings.retrieval_k,
                "agents": agents,
                "models": settings.llm_models,
                "effort": settings.llm_effort,
                "max_tokens": settings.llm_max_tokens,
                "limits": {
                    "max_claims": settings.run_max_claims,
                    "max_cost_usd": settings.run_max_cost_usd,
                },
                "demo": dict(demo) if demo is not None else None,
                "scope": scope,
                "provider": provider_identity(settings),
            }
        )

    def task(self, name: LLMTask) -> TaskSpec:
        key = name.value
        return TaskSpec(
            model=self.models[key],
            effort=self.effort.get(key),
            max_tokens=self.max_tokens[key],
            prompt_version=self.prompts[key],
        )


def provider_changed(config: RunConfig, settings: Settings) -> bool:
    """Lần chạy gắn một provider khác provider đang cấu hình (kể cả khi đã bỏ cấu hình LLM)."""
    return config.provider is not None and config.provider != provider_identity(settings)


def ensure_same_provider(config: RunConfig, settings: Settings) -> None:
    """`ConflictError(LLM_PROVIDER_CHANGED)` khi provider đã đổi: không trộn hai provider."""
    if provider_changed(config, settings):
        raise ConflictError(labels.LLM_PROVIDER_CHANGED, code="LLM_PROVIDER_CHANGED")


@dataclass(frozen=True)
class RunInfo:
    run_id: UUID
    document_id: UUID
    user_id: UUID
    parent_run_id: UUID | None
    config: RunConfig
    document_language: str

    def in_scope(self, reference_entry_id: UUID | None) -> bool:
        """Mục có thuộc phạm vi lần chạy không; lần chạy con bỏ qua cặp chưa liên kết."""
        scope = self.config.scope
        if scope is None:
            return True
        return reference_entry_id is not None and reference_entry_id in scope.reference_entry_ids


async def load_run_info(session: AsyncSession, run_id: UUID) -> RunInfo:
    """Đọc lần chạy kèm ngôn ngữ tài liệu; `NotFoundError` nếu lần chạy đã bị xóa."""
    row = (
        await session.execute(
            select(
                VerificationRun.document_id,
                VerificationRun.user_id,
                VerificationRun.parent_run_id,
                VerificationRun.config,
                Document.language,
            )
            .join(Document, Document.id == VerificationRun.document_id)
            .where(VerificationRun.id == run_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Không tìm thấy lần chạy")
    return RunInfo(
        run_id=run_id,
        document_id=row.document_id,
        user_id=row.user_id,
        parent_run_id=row.parent_run_id,
        config=RunConfig.load(row.config),
        document_language=row.language,
    )
