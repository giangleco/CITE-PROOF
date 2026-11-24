"""Gọi API thật: chỉ chạy khi CITEPROOF_LIVE_LLM=1 và có CITEPROOF_ANTHROPIC_API_KEY."""

import os
from typing import ClassVar

import anthropic
import pytest
from pydantic import Field

from citeproof.core.settings import Settings
from citeproof.llm.cache import InMemoryCacheStore, eval_namespace
from citeproof.llm.calls import InMemoryCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.context import PromptParts
from citeproof.llm.providers.anthropic import AnthropicProvider
from citeproof.llm.types import CacheMode, CallContext, OutputModel

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CITEPROOF_LIVE_LLM") != "1"
        or not os.environ.get("CITEPROOF_ANTHROPIC_API_KEY"),
        reason="cần CITEPROOF_LIVE_LLM=1 và CITEPROOF_ANTHROPIC_API_KEY",
    ),
]


class Capital(OutputModel):
    SCHEMA_VERSION: ClassVar[str] = "live_capital/1"
    city: str = Field(max_length=100)


async def test_live_haiku_structured_output() -> None:
    settings = Settings()
    assert settings.anthropic_api_key is not None
    sdk = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
    )
    call_logger = InMemoryCallLogger()
    client = LLMClient(
        AnthropicProvider(sdk, refusal_fallback=settings.anthropic_refusal_fallback),
        InMemoryCacheStore(),
        call_logger,
        mode=CacheMode.FRESH,
        prices=settings.llm_prices,
    )
    parts = PromptParts(
        task="live.capital",
        prompt_version="v1",
        system="Answer the question. Reply with the JSON object only.",
        user="What is the capital city of France?",
    )
    try:
        result = await client.complete_structured(
            parts=parts,
            schema=Capital,
            model="claude-haiku-5-5",
            ctx=CallContext(eval_namespace("live", "smoke"), None, None, "live"),
            max_tokens=1000,
            effort="low",
        )
    finally:
        await sdk.close()
    assert "paris" in result.value.city.lower()
    assert result.model.startswith("claude-haiku")
    assert call_logger.records[-1].input_tokens > 0
    assert call_logger.records[-1].cost_estimate > 0
