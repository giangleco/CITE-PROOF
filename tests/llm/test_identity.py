import pytest

from citeproof.core.settings import Settings
from citeproof.llm.identity import openai_identity, provider_identity

_MODELS = {task: "qwen3:8b" for task in Settings(_env_file=None).llm_models}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://API.OpenAI.com/v1/", "openai_compatible:api.openai.com"),
        ("http://localhost:11434/v1", "openai_compatible:localhost:11434"),
        ("https://openrouter.ai/api/v1", "openai_compatible:openrouter.ai"),
        ("http://[::1]:11434/v1", "openai_compatible:[::1]:11434"),
        ("http://[::1]/v1", "openai_compatible:[::1]"),
        ("https://x.com", "openai_compatible:x.com"),
        ("https://x.com:443/v1", "openai_compatible:x.com"),
        ("http://x.com:80/v1", "openai_compatible:x.com"),
        ("https://x.com:80/v1", "openai_compatible:x.com:80"),
        (" https://u:p@Host:8080/v1 ", "openai_compatible:host:8080"),
    ],
)
def test_openai_identity(url: str, expected: str) -> None:
    assert openai_identity(url) == expected


@pytest.mark.parametrize("url", ["localhost", "", "/v1"])
def test_openai_identity_requires_host(url: str) -> None:
    with pytest.raises(ValueError):
        openai_identity(url)


def test_provider_identity_anthropic() -> None:
    assert provider_identity(Settings(_env_file=None, anthropic_api_key="k")) == "anthropic"


def test_provider_identity_unconfigured_is_none() -> None:
    assert provider_identity(Settings(_env_file=None)) is None


def test_provider_identity_openai_compatible() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        openai_base_url="http://localhost:11434/v1",
        llm_models=_MODELS,
        llm_prices={"qwen3:8b": (0, 0)},
    )
    assert provider_identity(settings) == "openai_compatible:localhost:11434"
