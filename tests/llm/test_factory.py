from typing import Any

import pytest

from citeproof.core import defaults
from citeproof.core.settings import Settings
from citeproof.llm.factory import make_provider
from citeproof.llm.providers.anthropic import AnthropicProvider
from citeproof.llm.providers.openai_compatible import OpenAICompatibleProvider


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _openai(**overrides: Any) -> Settings:
    models = {task: "gpt-x" for task in defaults.LLM_MODELS}
    return _settings(
        llm_provider="openai_compatible",
        openai_base_url="http://Localhost:11434/v1",
        llm_models=models,
        llm_prices={"gpt-x": (1.0, 2.0)},
        **overrides,
    )


def test_none_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_ANTHROPIC_API_KEY", raising=False)
    assert make_provider(_settings()) is None
    assert make_provider(_settings(llm_provider="openai_compatible")) is None


def test_anthropic_provider() -> None:
    provider = make_provider(_settings(anthropic_api_key="sk-test"))
    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"


async def test_openai_compatible_provider_without_key() -> None:
    provider = make_provider(
        _openai(openai_structured_output="json_object", openai_send_reasoning_effort=True)
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == "openai_compatible:localhost:11434"
    sdk = provider._client
    assert sdk.api_key == "unused"
    assert str(sdk.base_url).lower().startswith("http://localhost:11434/v1")
    assert sdk.max_retries == 2 and sdk.timeout == 600.0
    await provider.aclose()


async def test_openai_compatible_provider_uses_key() -> None:
    provider = make_provider(_openai(openai_api_key="sk-real", openai_timeout_seconds=30.0))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._client.api_key == "sk-real"
    assert provider._client.timeout == 30.0
    await provider.aclose()
