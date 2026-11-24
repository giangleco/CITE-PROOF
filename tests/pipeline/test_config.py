"""RunConfig, TaskSpec, RunInfo (spec §6.2)."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import AgentName, Language, LLMTask, StageName, Verifier
from citeproof.core.errors import NotFoundError
from citeproof.core.settings import Settings
from citeproof.db.models import VerificationRun
from citeproof.pipeline.config import (
    AgentConfig,
    RunConfig,
    RunInfo,
    RunLimits,
    RunScope,
    TaskSpec,
    load_run_info,
)
from citeproof.pipeline.runs import create_run
from tests.db.factories import make_document

SessionFactory = async_sessionmaker[AsyncSession]
A1 = AgentName.REFERENCE_RESOLVER.value
A2 = AgentName.EVIDENCE_INVESTIGATOR.value


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _scope(*ids: uuid.UUID) -> RunScope:
    return RunScope(kind="upload", reference_entry_ids=list(ids), from_stage=StageName.S6)


def _info(scope: RunScope | None) -> RunInfo:
    return RunInfo(
        run_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        parent_run_id=None,
        config=RunConfig(scope=scope),
        document_language="vi",
    )


def test_load_old_config_shape_gets_defaults() -> None:
    cfg = RunConfig.load({"variant": "V2"})
    assert cfg.variant is Verifier.V2
    assert cfg.cross_lingual == "X2"
    assert cfg.retrieval_k == 6
    assert cfg.agents[A1] == AgentConfig(
        enabled=False,
        max_steps=6,
        max_context_tokens=10_000,
        max_total_input_tokens=40_000,
        max_wall_seconds=120.0,
    )
    assert cfg.agents[A2] == AgentConfig(
        enabled=False,
        max_steps=8,
        max_context_tokens=20_000,
        max_total_input_tokens=90_000,
        max_wall_seconds=180.0,
    )
    assert cfg.tool_catalogs == {"meta": "v1", "source": "v1"}
    assert cfg.limits == RunLimits(max_claims=150, max_cost_usd=5.0)
    assert cfg.demo is None
    assert cfg.scope is None


def test_load_empty_or_missing_config_and_extra_keys() -> None:
    assert RunConfig.load(None) == RunConfig()
    assert RunConfig.load({}) == RunConfig()
    assert RunConfig.load({"variant": "V0", "unknown": 1}).variant is Verifier.V0


def test_load_fills_missing_keys_from_code_not_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lần chạy cũ tiếp tục sau lỗi giữ đúng model đã đóng băng, kể cả khi cấu hình máy đổi.
    monkeypatch.setenv("CITEPROOF_LLM_MODELS", '{"verdict.judge": "claude-opus-5-5"}')
    monkeypatch.setenv("CITEPROOF_RETRIEVAL_K", "9")
    monkeypatch.setenv("CITEPROOF_RUN_MAX_CLAIMS", "3")
    cfg = RunConfig.load({"variant": "V2"})
    assert cfg.models["verdict.judge"] == "claude-sonnet-5-5"
    assert cfg.retrieval_k == 6
    assert cfg.limits.max_claims == 150


def test_load_merges_partial_tables_with_defaults() -> None:
    cfg = RunConfig.load(
        {
            "models": {"verdict.judge": "claude-opus-5-5"},
            "agents": {A2: {"enabled": True}},
            "limits": {"max_claims": 10},
        }
    )
    assert cfg.models["verdict.judge"] == "claude-opus-5-5"
    assert cfg.models["claim.extract"] == "claude-haiku-5-5"
    assert cfg.agents[A2].enabled is True
    assert cfg.agents[A2].max_steps == 8
    assert cfg.agents[A1].enabled is False
    assert cfg.limits == RunLimits(max_claims=10, max_cost_usd=5.0)


@pytest.mark.parametrize(
    "raw",
    [{"variant": "V9"}, {"cross_lingual": "X3"}, {"scope": {"kind": "other"}}],
)
def test_load_rejects_invalid_values(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RunConfig.load(raw)


def test_resolve_pins_models_and_prompts() -> None:
    cfg = RunConfig.resolve(_settings(), variant=Verifier.V2)
    assert cfg.models["verdict.judge"] == "claude-sonnet-5-5"
    assert cfg.prompts["verdict.judge"] == "v1"
    assert cfg.prompts == {task.value: "v1" for task in LLMTask}


def test_resolve_copies_settings() -> None:
    base = _settings()
    settings = _settings(
        llm_models={**base.llm_models, "verdict.judge": "claude-opus-5-5"},
        llm_effort={**base.llm_effort, "claim.extract": "medium"},
        llm_max_tokens={**base.llm_max_tokens, "claim.extract": 3000},
        run_max_claims=20,
        run_max_cost_usd=1.5,
        retrieval_k=4,
    )
    scope = _scope(uuid.uuid4())
    demo = {"step_delay_s": 1.5, "fail_at": "S5"}
    cfg = RunConfig.resolve(settings, variant=Verifier.V1, demo=demo, scope=scope)
    assert cfg.variant is Verifier.V1
    assert cfg.models == settings.llm_models
    assert cfg.effort == settings.llm_effort
    assert cfg.max_tokens == settings.llm_max_tokens
    assert cfg.limits == RunLimits(max_claims=20, max_cost_usd=1.5)
    assert cfg.retrieval_k == 4
    assert cfg.demo == demo
    assert cfg.scope == scope


def test_resolve_fills_tasks_missing_from_settings() -> None:
    settings = _settings(llm_models={"verdict.judge": "claude-opus-5-5"})
    cfg = RunConfig.resolve(settings, variant=Verifier.V2)
    assert cfg.models["verdict.judge"] == "claude-opus-5-5"
    assert cfg.models["reference.parse"] == "claude-haiku-5-5"


@pytest.mark.parametrize(
    ("variant", "a2_enabled"),
    [
        (Verifier.V0, False),
        (Verifier.V1, False),
        (Verifier.V2, False),
        (Verifier.V3, False),
        (Verifier.V4, True),
        (Verifier.V2_A2, True),
    ],
)
def test_resolve_enables_a2_only_for_agentic_variants(variant: Verifier, a2_enabled: bool) -> None:
    cfg = RunConfig.resolve(_settings(), variant=variant)
    assert cfg.agents[A2].enabled is a2_enabled
    assert cfg.agents[A1].enabled is False


def test_resolved_config_round_trips_through_json() -> None:
    cfg = RunConfig.resolve(
        _settings(), variant=Verifier.V2_A2, demo={"step_delay_s": 0}, scope=_scope(uuid.uuid4())
    )
    raw = cfg.model_dump(mode="json")
    assert raw["variant"] == "V2+A2"
    assert RunConfig.load(json.loads(json.dumps(raw))) == cfg


def test_task_spec() -> None:
    cfg = RunConfig.resolve(_settings(), variant=Verifier.V2)
    assert cfg.task(LLMTask.CLAIM_EXTRACT) == TaskSpec("claude-haiku-5-5", "low", 4000, "v1")
    assert cfg.task(LLMTask.VERDICT_JUDGE) == TaskSpec("claude-sonnet-5-5", "high", 16000, "v1")


def test_every_task_has_a_spec() -> None:
    cfg = RunConfig()
    assert {cfg.task(task).prompt_version for task in LLMTask} == {"v1"}


def test_settings_and_run_config_share_defaults() -> None:
    settings = _settings()
    cfg = RunConfig()
    assert cfg.models == settings.llm_models
    assert cfg.effort == settings.llm_effort
    assert cfg.max_tokens == settings.llm_max_tokens
    assert cfg.retrieval_k == settings.retrieval_k
    assert cfg.limits == RunLimits(
        max_claims=settings.run_max_claims, max_cost_usd=settings.run_max_cost_usd
    )


def test_default_tables_are_not_shared() -> None:
    first = RunConfig()
    first.models["verdict.judge"] = "x"
    first.agents[A2].enabled = True
    second = RunConfig()
    assert second.models["verdict.judge"] == "claude-sonnet-5-5"
    assert second.agents[A2].enabled is False


def test_in_scope() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    scoped = _info(_scope(a))
    assert scoped.in_scope(a) is True
    assert scoped.in_scope(b) is False
    assert scoped.in_scope(None) is False
    unscoped = _info(None)
    assert unscoped.in_scope(b) is True
    assert unscoped.in_scope(None) is True


@pytest.mark.db
async def test_load_run_info(session_factory: SessionFactory, clean_db: None) -> None:
    scope = _scope(uuid.uuid4())
    config = RunConfig.resolve(_settings(), variant=Verifier.V1, scope=scope)
    async with session_factory() as session:
        doc = await make_document(session)
        doc.language = Language.VI.value
        parent_id = await create_run(
            session, document_id=doc.id, user_id=doc.user_id, config={"variant": "V2"}
        )
        run_id = await create_run(
            session,
            document_id=doc.id,
            user_id=doc.user_id,
            config=config.model_dump(mode="json"),
        )
        await session.execute(
            update(VerificationRun)
            .where(VerificationRun.id == run_id)
            .values(parent_run_id=parent_id)
        )
        await session.commit()

    async with session_factory() as session:
        info = await load_run_info(session, run_id)
        parent = await load_run_info(session, parent_id)
    assert info == RunInfo(
        run_id=run_id,
        document_id=doc.id,
        user_id=doc.user_id,
        parent_run_id=parent_id,
        config=config,
        document_language="vi",
    )
    assert parent.config == RunConfig.load({"variant": "V2"})
    assert parent.parent_run_id is None


@pytest.mark.db
async def test_load_run_info_missing_run(session_factory: SessionFactory, clean_db: None) -> None:
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await load_run_info(session, uuid.uuid4())
