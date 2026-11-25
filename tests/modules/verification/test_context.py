"""Prompt W5 (`verdict.judge`, `verdict.judge_closed_book`; 07a §4.8.3) và `judge`."""

import json
import uuid

import pytest

from citeproof.core.enums import ChunkRegion, ClaimStatus, ContentLevelUsed, EvidenceRelation
from citeproof.core.errors import LLMOutputError
from citeproof.llm.cache import InMemoryCacheStore
from citeproof.llm.calls import InMemoryCallLogger
from citeproof.llm.client import LLMClient
from citeproof.llm.prompts import PromptLoader
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import CallContext, ProviderResponse
from citeproof.modules.verification.context import JudgeInput, RefMeta, build_judge_prompt
from citeproof.modules.verification.judge import judge
from citeproof.modules.verification.rules import ChunkInfo
from citeproof.pipeline.config import TaskSpec
from citeproof.pipeline.deps import PROMPTS_ROOT

SPEC = TaskSpec(model="claude-sonnet-5-5", effort="high", max_tokens=16000, prompt_version="v1")
CTX = CallContext(namespace="eval:test@abc", document_id=None, run_id=None, stage="S8")
REF = RefMeta(title="Encoder Paper", authors=("Nguyễn Văn A", "B. Smith"), year=2020, venue="ACL")


def _chunk(n: int, text: str, region: ChunkRegion = ChunkRegion.BODY) -> ChunkInfo:
    return ChunkInfo(
        chunk_id=uuid.uuid4(),
        short_id=f"chunk_{n}",
        text=text,
        page=n,
        section="5 Experiments",
        region=region,
    )


def _input(
    claim: str = "Độ chính xác đạt 95,7%",
    chunks: tuple[ChunkInfo, ...] = (),
    language: str = "vi",
    level: ContentLevelUsed = ContentLevelUsed.FULL_TEXT,
) -> JudgeInput:
    return JudgeInput(
        ref=REF,
        content_level=level,
        claim_text=claim,
        claim_language="vi",
        context_text="Câu trước. Câu sau.",
        chunks=chunks,
        explanation_language="vi" if language == "vi" else "en",
    )


def test_judge_prompt_layout() -> None:
    prompts = PromptLoader(PROMPTS_ROOT)
    first = _input(chunks=(_chunk(1, "Top ranked passage"), _chunk(2, "Second passage")))
    second = _input(claim="Another claim <b>", chunks=(_chunk(1, "Other text & more"),))
    p1 = build_judge_prompt(prompts, SPEC, first, closed_book=False)
    p2 = build_judge_prompt(prompts, SPEC, second, closed_book=False)
    assert p1.task == "verdict.judge" and p1.prompt_version == "v1"
    assert p1.system == p2.system
    assert "Top ranked passage" not in p1.system
    user = p1.user
    assert '<source_chunks untrusted="true">' in user
    start = user.index('<source_chunks untrusted="true">')
    end = user.index("</source_chunks>")
    block = user[start:end]
    assert block.index('id="chunk_1"') < block.index('id="chunk_2"')
    assert block.index("Top ranked passage") < block.index("Second passage")
    assert 'page="1"' in block and 'section="5 Experiments"' in block and 'region="BODY"' in block
    assert '<claim lang="vi" untrusted="true">' in user
    assert "Độ chính xác đạt 95,7%" in user
    assert '<context untrusted="true">' in user
    assert 'content_level="FULL_TEXT"' in user and "Encoder Paper" in user
    assert user.rstrip().splitlines()[-1].count("Vietnamese") == 1
    # Dữ liệu được escape: không đóng được data block.
    assert "Another claim &lt;b&gt;" in p2.user and "Other text &amp; more" in p2.user


def test_judge_prompt_english_reminder() -> None:
    prompts = PromptLoader(PROMPTS_ROOT)
    parts = build_judge_prompt(prompts, SPEC, _input(language="en"), closed_book=False)
    last = parts.user.rstrip().splitlines()[-1]
    assert "English" in last and "Vietnamese" not in last


def test_closed_book_prompt_has_no_chunks() -> None:
    prompts = PromptLoader(PROMPTS_ROOT)
    parts = build_judge_prompt(prompts, SPEC, _input(level=ContentLevelUsed.NONE), closed_book=True)
    assert parts.task == "verdict.judge_closed_book"
    assert "<source_chunks" not in parts.user and "chunk_1" not in parts.user
    assert "No source text is provided" in parts.system
    assert "evidence to []" in parts.user


def test_closed_book_rejects_chunks() -> None:
    prompts = PromptLoader(PROMPTS_ROOT)
    with pytest.raises(ValueError):
        build_judge_prompt(prompts, SPEC, _input(chunks=(_chunk(1, "x"),)), closed_book=True)


def _verdict(**fields: object) -> ProviderResponse:
    body = {
        "schema_version": "verdict/2",
        "status": "SUPPORTED",
        "evidence": [],
        "abstain_cause": None,
        "explanation": "ok",
        "limitations": None,
        **fields,
    }
    return ProviderResponse(json.dumps(body), 100, 20, "end_turn", model="claude-sonnet-5-5-alt")


async def test_judge_returns_verdict_served_model_and_unescapes_quotes() -> None:
    provider = ScriptedProvider(
        [
            _verdict(
                evidence=[
                    {
                        "chunk_id": "chunk_1",
                        "quote": "p &lt; 0.05 &amp; more",
                        "relation": "SUPPORTS",
                    }
                ]
            )
        ]
    )
    llm = LLMClient(provider, InMemoryCacheStore(), InMemoryCallLogger())
    result = await judge(
        llm,
        PromptLoader(PROMPTS_ROOT),
        SPEC,
        CTX,
        _input(chunks=(_chunk(1, "p < 0.05 & more"),)),
    )
    assert result.verdict.status is ClaimStatus.SUPPORTED
    assert result.verdict.evidence[0].quote == "p < 0.05 & more"
    assert result.verdict.evidence[0].relation is EvidenceRelation.SUPPORTS
    assert result.model == "claude-sonnet-5-5-alt"
    assert result.prompt_version == "v1"
    (request,) = provider.requests
    assert request.model == "claude-sonnet-5-5"
    assert request.effort == "high" and request.max_tokens == 16000


async def test_judge_invalid_output_raises() -> None:
    bad = ProviderResponse("{}", 1, 1, "end_turn")
    llm = LLMClient(ScriptedProvider([bad, bad]), InMemoryCacheStore(), InMemoryCallLogger())
    with pytest.raises(LLMOutputError):
        await judge(llm, PromptLoader(PROMPTS_ROOT), SPEC, CTX, _input(), closed_book=True)
