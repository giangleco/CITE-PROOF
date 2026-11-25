"""Khung công cụ agent (07a §4.6.1): lỗi, kết quả, catalog YAML, submit, ngân sách, factory."""

import dataclasses
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.settings import Settings
from citeproof.llm.agent_runner import AgentBudget, AgentRunner
from citeproof.llm.factory import make_agent_runner
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.tools import SubmitTool, Tool, ToolError, ToolResult, load_tool_catalog
from citeproof.llm.types import OutputModel, ToolSpec, TurnRequest, TurnResponse
from citeproof.pipeline.config import AgentConfig
from tests.pipeline.helpers import RoutedProvider, make_test_deps


class Answer(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "answer/1"
    answer: str


class Args(BaseModel):
    query: str


class Echo:
    name = "echo"
    description = "Echo."
    input_model: type[BaseModel] = Args

    async def __call__(self, args: BaseModel) -> ToolResult | ToolError:
        return ToolResult("ok")


def test_tool_protocol_and_values() -> None:
    tool: Tool = Echo()
    assert tool.name == "echo"
    error = ToolError(code="X", message="m", fix="f")
    assert (error.code, error.message, error.fix) == ("X", "m", "f")
    with pytest.raises(dataclasses.FrozenInstanceError):
        error.code = "Y"  # type: ignore[misc]
    assert ToolResult("c").content == "c"


def test_submit_tool_runs_check() -> None:
    submit = SubmitTool(
        name="submit_answer",
        description="Submit.",
        model=Answer,
        check=lambda v: [ToolError("E", "m", "f")] if not v.answer else [],
    )
    assert submit.check(Answer(answer="")) == [ToolError("E", "m", "f")]
    assert submit.check(Answer(answer="x")) == []


def _write(root: Path, catalog: str, version: str, text: str) -> None:
    path = root / "tools" / catalog / f"{version}.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def test_load_tool_catalog(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "meta",
        "v1",
        "meta_search: >\n  Search records.\n  Two lines.\nmeta_get: Get one.\n",
    )
    assert load_tool_catalog(tmp_path, "meta", "v1") == {
        "meta_search": "Search records. Two lines.",
        "meta_get": "Get one.",
    }


def test_load_tool_catalog_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_tool_catalog(tmp_path, "meta", "v9")


@pytest.mark.parametrize("text", ["- a\n- b\n", "meta_get: 3\n", "'': x\n", "{}\n"])
def test_load_tool_catalog_rejects_bad_shape(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "meta", "v1", text)
    with pytest.raises(ValueError):
        load_tool_catalog(tmp_path, "meta", "v1")


def test_budget_from_config() -> None:
    cfg = AgentConfig(
        max_steps=6, max_context_tokens=10, max_total_input_tokens=40, max_wall_seconds=1.5
    )
    assert AgentBudget.from_config(cfg) == AgentBudget(6, 10, 40, 1.5, 2)


def _request() -> TurnRequest:
    return TurnRequest(
        model="m",
        system="s",
        messages=({"role": "user", "content": "u"},),
        tools=(ToolSpec("echo", "Echo.", Args.model_json_schema()),),
        max_tokens=10,
        effort=None,
    )


async def test_scripted_provider_turns() -> None:
    response = TurnResponse(
        content=({"type": "text", "text": "hi"},),
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
        model="m",
    )
    provider = ScriptedProvider(turns=[response])
    assert await provider.complete_turn(_request()) == response
    assert provider.turn_requests == [_request()]
    with pytest.raises(AssertionError):
        await provider.complete_turn(_request())


async def test_routed_provider_turns() -> None:
    response = TurnResponse(
        content=(), stop_reason="end_turn", input_tokens=1, output_tokens=1, model="m"
    )
    provider = RoutedProvider({}, turns=[response])
    assert await provider.complete_turn(_request()) == response
    with pytest.raises(AssertionError):
        await provider.complete_turn(_request())


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_make_agent_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_ANTHROPIC_API_KEY", raising=False)
    assert make_agent_runner(_settings(), async_sessionmaker()) is None
    assert isinstance(
        make_agent_runner(_settings(anthropic_api_key="sk-test"), async_sessionmaker()), AgentRunner
    )
    given = ScriptedProvider()
    runner = make_agent_runner(_settings(), async_sessionmaker(), provider=given)
    assert isinstance(runner, AgentRunner) and runner._provider is given


async def test_make_test_deps_sets_agents(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    deps = make_test_deps(settings, session_factory, provider=ScriptedProvider())
    assert isinstance(deps.agents, AgentRunner)
    plain = make_test_deps(settings, session_factory)
    assert plain.agents is None
    await deps.http.aclose()
    await plain.http.aclose()
