"""Bộ đánh giá chạy và phát lại trên mọi provider (spec §5.6a): provider, định danh, báo cáo."""

from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.db.models import VerificationRun
from citeproof.evaluation.__main__ import _parser
from citeproof.evaluation.runner import (
    ReplayOnlyProvider,
    _provider_for,
    run_eval,
)
from citeproof.llm.types import CacheMode
from tests.evaluation.test_harness import MINI, V2_X2, _result_dir, routed

SessionFactory = async_sessionmaker[AsyncSession]

OPENAI = "openai_compatible:llm.example:8000"


@pytest.fixture
def eval_settings(settings: Settings, database_url: str) -> Settings:
    return settings.model_copy(update={"eval_database_url": database_url, "grobid_url": ""})


def _settings(tmp_path: Path, **update: object) -> Settings:
    base = Settings(_env_file=None, storage_dir=tmp_path / "storage")  # type: ignore[call-arg]
    return base.model_copy(update={"anthropic_api_key": None, **update})


def _openai(tmp_path: Path) -> Settings:
    return _settings(
        tmp_path,
        llm_provider="openai_compatible",
        openai_base_url="http://llm.example:8000/v1",
    )


@pytest.mark.parametrize("mode", [CacheMode.READ_WRITE, CacheMode.FRESH])
def test_unconfigured_llm_message_is_provider_neutral(tmp_path: Path, mode: CacheMode) -> None:
    with pytest.raises(InputError) as info:
        _provider_for(_settings(tmp_path), mode, None)
    assert info.value.code == "LLM_NOT_CONFIGURED"
    assert "ANTHROPIC" not in str(info.value).upper()


def test_read_write_openai_lets_factory_build_provider(tmp_path: Path) -> None:
    assert _provider_for(_openai(tmp_path), CacheMode.READ_WRITE, None) is None


def test_read_write_anthropic_configured_returns_none(tmp_path: Path) -> None:
    settings = _settings(tmp_path, anthropic_api_key=SecretStr("k"))
    assert _provider_for(settings, CacheMode.READ_WRITE, None) is None


def test_replay_name_follows_configured_identity(tmp_path: Path) -> None:
    replay = _provider_for(_openai(tmp_path), CacheMode.REPLAY_ONLY, None)
    assert isinstance(replay, ReplayOnlyProvider)
    assert replay.name == OPENAI


def test_replay_defaults_to_anthropic_when_unconfigured(tmp_path: Path) -> None:
    replay = _provider_for(_settings(tmp_path), CacheMode.REPLAY_ONLY, None)
    assert replay is not None
    assert replay.name == "anthropic"


def test_replay_identity_override(tmp_path: Path) -> None:
    replay = _provider_for(
        _settings(tmp_path),
        CacheMode.REPLAY_ONLY,
        None,
        identity_override="openai_compatible:x",
    )
    assert replay is not None
    assert replay.name == "openai_compatible:x"


def test_cli_accepts_provider_identity() -> None:
    args = _parser().parse_args(
        ["run", "--dataset", "d", "--config", "c", "--mode", "replay", "--provider-identity", "a:b"]
    )
    assert args.provider_identity == "a:b"


async def test_replay_with_other_identity_runs_and_reports_it(
    eval_settings: Settings, session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    provider = routed()
    provider.name = "routed"
    common = {
        "dataset_path": MINI,
        "config_path": V2_X2,
        "split": None,
        "dump": False,
    }
    first = await run_eval(
        eval_settings,
        mode=CacheMode.READ_WRITE,
        out_dir=tmp_path / "a",
        provider=provider,
        **common,
    )
    second = await run_eval(
        eval_settings,
        mode=CacheMode.REPLAY_ONLY,
        out_dir=tmp_path / "b",
        provider_identity="routed",
        **common,
    )
    assert second.metrics == first.metrics
    assert second.provider == "routed"
    markdown = (_result_dir(tmp_path / "b", "v2-x2") / "report.md").read_text(encoding="utf-8")
    assert "- Provider: `routed`" in markdown
    # Lần chạy phát lại không gắn provider khác với provider đang cấu hình (worker không chặn).
    async with session_factory() as session:
        raw = await session.scalar(
            select(VerificationRun.config).where(VerificationRun.id == second.run_id)
        )
    assert raw is not None
    assert raw.get("provider") is None
