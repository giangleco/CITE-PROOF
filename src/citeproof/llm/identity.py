"""Định danh provider LLM, dùng cho cache key và kiểm tra đổi provider (thuần, không SDK)."""

from citeproof.core.settings import Settings, base_url_authority


def openai_identity(base_url: str) -> str:
    """`openai_compatible:<host>[:port]`; ValueError khi base URL không có host."""
    return f"openai_compatible:{base_url_authority(base_url)}"


def provider_identity(settings: Settings) -> str | None:
    if not settings.llm_configured:
        return None
    if settings.llm_provider == "anthropic":
        return "anthropic"
    assert settings.openai_base_url is not None
    return openai_identity(settings.openai_base_url)
