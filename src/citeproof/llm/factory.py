"""Dựng `LLMClient` và `AgentRunner` thật từ cấu hình (ADR-006, ADR-010)."""

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.settings import Settings
from citeproof.llm.agent_runner import AgentRunner
from citeproof.llm.cache import PgCacheStore
from citeproof.llm.calls import PgCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.providers.anthropic import AnthropicProvider
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.types import CacheMode


def make_anthropic_provider(settings: Settings) -> AnthropicProvider | None:
    """Provider Anthropic từ khóa API; None khi chưa có khóa."""
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value():
        return None
    sdk = anthropic.AsyncAnthropic(
        api_key=key.get_secret_value(),
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
    )
    return AnthropicProvider(sdk, refusal_fallback=settings.anthropic_refusal_fallback)


def make_llm_client(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mode: CacheMode = CacheMode.READ_WRITE,
    provider: LLMProvider | None = None,
) -> LLMClient | None:
    """None khi chưa có khóa API: các bước LLM giảm cấp tất định (`LLM_NOT_CONFIGURED`).

    `provider` thay cho Anthropic (bộ đánh giá: provider giả trong test, provider chỉ phát lại);
    khi có `provider` thì không cần khóa API.
    """
    if provider is None:
        provider = make_anthropic_provider(settings)
        if provider is None:
            return None
    return LLMClient(
        provider,
        PgCacheStore(session_factory),
        PgCallLogger(session_factory),
        mode=mode,
        prices=settings.llm_prices,
    )


def make_agent_runner(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mode: CacheMode = CacheMode.READ_WRITE,
    provider: LLMProvider | None = None,
) -> AgentRunner | None:
    """Như `make_llm_client`, cho agent: None khi chưa có khóa API và không truyền `provider`.

    `open_deps` truyền cùng một provider cho cả `LLMClient` và `AgentRunner` (một client HTTP).
    """
    if provider is None:
        provider = make_anthropic_provider(settings)
        if provider is None:
            return None
    return AgentRunner(
        provider,
        PgCacheStore(session_factory),
        PgCallLogger(session_factory),
        session_factory,
        mode=mode,
        prices=settings.llm_prices,
    )
