"""W3 `claim.translate`: prompt, schema và kiểm tra số (spec §7.7, §8.2)."""

import json

import pytest

from citeproof.core.enums import LLMTask
from citeproof.core.errors import LLMOutputError
from citeproof.llm.cache import InMemoryCacheStore
from citeproof.llm.calls import InMemoryCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.schema import strict_output_schema
from citeproof.llm.types import CallContext, ProviderResponse
from citeproof.modules.retrieval.translate import (
    TranslatedClaim,
    numbers_preserved,
    translate_claim,
)
from citeproof.pipeline.config import RunConfig
from citeproof.pipeline.deps import PROMPTS_ROOT

PROMPTS = PromptLoader(PROMPTS_ROOT)
SPEC = RunConfig().task(LLMTask.CLAIM_TRANSLATE)
CTX = CallContext(namespace="eval:test@abc", document_id=None, run_id=None, stage="S7")


def _client(*responses: ProviderResponse) -> tuple[LLMClient, ScriptedProvider]:
    provider = ScriptedProvider(list(responses))
    return LLMClient(provider, InMemoryCacheStore(), InMemoryCallLogger()), provider


def _response(text_en: str, terms: list[str] | None = None) -> ProviderResponse:
    body = json.dumps({"text_en": text_en, "kept_terms": terms or []})
    return ProviderResponse(body, 10, 5, "end_turn")


@pytest.mark.parametrize(
    ("original", "translated", "ok"),
    [
        ("tăng 12%", "increases by 12%", True),
        ("tăng 12%", "increases", False),
        ("đạt 95,7%", "reaches 95.7%", True),
        ("đạt 95,7%", "reaches 95,7%", True),
        ("đạt 95,7%", "reaches 95%", False),
        ("không có số", "no numbers", True),
        ("năm 2020 và 3 GB", "in 2020 and 3 GB", True),
    ],
)
def test_numbers_preserved(original: str, translated: str, ok: bool) -> None:
    assert numbers_preserved(original, translated) is ok


def test_schema_is_strict_compatible() -> None:
    schema = strict_output_schema(TranslatedClaim.model_json_schema())
    assert "oneOf" not in json.dumps(schema)
    assert TranslatedClaim.SCHEMA_VERSION == "translated_claim/1"


def test_prompt_keeps_terms_and_marks_data_untrusted() -> None:
    parts = PROMPTS.render("claim.translate", "v1", claim='<claim untrusted="true">\nx\n</claim>')
    assert "verbatim" in parts.system and "untrusted" in parts.system
    assert '<claim untrusted="true">' in parts.user


def test_prompt_is_english_file() -> None:
    assert (PROMPTS_ROOT / "claim.translate" / "v1.md").is_file()


async def test_translate_claim_returns_text_via_client() -> None:
    llm, provider = _client(_response("The BERT model reaches 95.7%.", ["BERT"]))
    out = await translate_claim(llm, PROMPTS, SPEC, CTX, "Mô hình BERT đạt 95,7%.")
    assert out == "The BERT model reaches 95.7%."
    [request] = provider.requests
    assert request.model == "claude-haiku-5-5"
    assert "Mô hình BERT" in request.messages[0]["content"]


async def test_translate_claim_drops_missing_number() -> None:
    llm, _ = _client(_response("increases"))
    assert await translate_claim(llm, PROMPTS, SPEC, CTX, "tăng 12%") is None


async def test_translate_claim_propagates_bad_output() -> None:
    bad = ProviderResponse('{"nope": 1}', 1, 1, "end_turn")
    llm, _ = _client(bad, bad)
    with pytest.raises(LLMOutputError):
        await translate_claim(llm, PROMPTS, SPEC, CTX, "tăng 12%")


@pytest.mark.parametrize(
    ("original", "translated", "ok"),
    [
        ("năm 2012", "in 12", False),
        ("95,7%", "95.7 percent", True),
        ("12 và 2012", "2012 and 12", True),
        ("tăng 12%", "increased 112%", False),
        ("đạt 5", "reached 5.5", False),
    ],
)
def test_numbers_preserved_compares_number_sets(original: str, translated: str, ok: bool) -> None:
    assert numbers_preserved(original, translated) is ok
