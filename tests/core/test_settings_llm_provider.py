import logging

import pytest
from pydantic import ValidationError

from citeproof.core.settings import Settings

_TASKS = list(Settings(_env_file=None).llm_models)
_LOCAL = {task: "qwen3:8b" for task in _TASKS}


def test_defaults_are_anthropic() -> None:
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "anthropic"
    assert settings.openai_timeout_seconds == 600.0
    assert settings.openai_max_retries == 2
    assert settings.openai_structured_output == "json_schema"
    assert settings.openai_send_reasoning_effort is False
    assert settings.llm_configured is False


def test_llm_configured() -> None:
    assert Settings(_env_file=None, anthropic_api_key="k").llm_configured
    assert not Settings(_env_file=None, anthropic_api_key="").llm_configured
    local = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        openai_base_url="http://localhost:11434/v1",
        llm_models=_LOCAL,
        llm_prices={"qwen3:8b": (0, 0)},
    )
    assert local.llm_configured


def test_openai_requires_custom_models() -> None:
    with pytest.raises(ValidationError, match="CITEPROOF_LLM_MODELS"):
        Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            openai_base_url="http://localhost:11434/v1",
        )


def test_configured_models_must_have_prices() -> None:
    with pytest.raises(ValidationError, match="qwen3:8b"):
        Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            openai_base_url="http://localhost:11434/v1",
            llm_models=_LOCAL,
        )


def test_local_zero_price_accepted() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        openai_base_url="http://localhost:11434/v1",
        llm_models=_LOCAL,
        llm_prices={"qwen3:8b": (0, 0)},
    )
    assert settings.llm_prices["qwen3:8b"] == (0, 0)


def test_unconfigured_skips_price_check() -> None:
    settings = Settings(_env_file=None, llm_models=_LOCAL)
    assert not settings.llm_configured


def test_openai_base_url_validated_at_startup() -> None:
    with pytest.raises(ValidationError, match="openai_base_url"):
        Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            openai_base_url="localhost",
            llm_models=_LOCAL,
            llm_prices={"qwen3:8b": (0, 0)},
        )


def test_missing_provider_name_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            openai_base_url="http://localhost:11434/v1",
            llm_models=_LOCAL,
            llm_prices={"qwen3:8b": (0, 0)},
        )
    assert "CITEPROOF_LLM_PROVIDER_NAME" in caplog.text
