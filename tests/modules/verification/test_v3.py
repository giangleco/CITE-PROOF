"""V3 — W4 tách ý, truy xuất và W5 theo từng ý, gộp tất định (spec §7.8, QĐ-48; T-504)."""

import json
from collections.abc import Sequence

import pytest
from sqlalchemy import select, update

from citeproof.core.enums import ClaimStatus, ContentLevelUsed, UnverifiableReason
from citeproof.core.settings import Settings
from citeproof.db.models import Claim, LLMCall, RetrievalResult
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import ProviderResponse
from tests.modules.verification.test_verify_stage import (
    SessionFactory,
    _by_claim,
    _evidence,
    _rows,
    _run,
    _setup,
    _verdict,
)

X1 = {"cross_lingual": "X1"}
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
CLAIM = "Encoder đạt 95,7% và các kết nối residual ổn định mô hình sâu"
S1 = "The encoder accuracy reaches 95.7%"
S2 = "Residual connections stabilise deep models."


def _w4(*texts: str) -> ProviderResponse:
    body = {"subclaims": [{"id": f"s{i}", "text": t} for i, t in enumerate(texts, start=1)]}
    return ProviderResponse(json.dumps(body), 100, 50, "end_turn")


W4_TWO = _w4("encoder accuracy 95.7% benchmark", "residual connections stabilise deep models")
V_S1 = _verdict(evidence=[("chunk_1", S1, "SUPPORTS")], explanation="Đúng số liệu.")
V_S2 = _verdict(evidence=[("chunk_1", S2, "SUPPORTS")], explanation="Đúng nhận định.")


async def _variants(sf: SessionFactory, claim_id: object) -> set[str]:
    async with sf() as s:
        rows = await s.execute(
            select(RetrievalResult.query_variant).where(RetrievalResult.claim_id == claim_id)
        )
    return {v for (v,) in rows}


@pytest.mark.db
async def test_v3_writes_subclaim_retrieval_rows(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, [CLAIM], variant="V3", retrieved=(), config_extra=X1)
    provider = ScriptedProvider([W4_TWO, V_S1, V_S2])
    await _run(session_factory, settings, provider)
    assert await _variants(session_factory, setup.claim_ids[0]) == {"subclaim:s1", "subclaim:s2"}
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value
    assert row.verifier == "V3"
    assert (row.model, row.prompt_version, row.schema_version) == (SONNET, "v1", "verdict/2")
    assert row.content_level_used == ContentLevelUsed.FULL_TEXT.value
    assert row.explanation == "s1: Đúng số liệu.; s2: Đúng nhận định."
    evidence = await _evidence(session_factory, row.id)
    assert [e.source_chunk_id for e in evidence] == [setup.chunk_ids[1], setup.chunk_ids[2]]
    models = [r.model for r in provider.requests]
    assert models == [HAIKU, SONNET, SONNET]
    async with session_factory() as s:
        stages = (
            await s.execute(select(LLMCall.stage).where(LLMCall.run_id == setup.run_id))
        ).all()
    assert [st for (st,) in stages] == ["S8", "S8", "S8"]


@pytest.mark.db
async def test_v3_mixed_subclaims_partially_supported_flagged(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, [CLAIM], variant="V3", retrieved=(), config_extra=X1)
    unsupported = _verdict(
        "UNSUPPORTED", [("chunk_1", S2, "RELATED")], explanation="Không có nội dung này."
    )
    await _run(session_factory, settings, ScriptedProvider([W4_TWO, V_S1, unsupported]))
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.PARTIALLY_SUPPORTED.value
    assert row.needs_user_review is True


@pytest.mark.db
@pytest.mark.parametrize("failure", ["invalid", "empty"])
async def test_w4_failure_falls_back_to_single_subclaim(
    session_factory: SessionFactory, clean_db: None, settings: Settings, failure: str
) -> None:
    setup = await _setup(session_factory, [CLAIM], variant="V3", retrieved=(), config_extra=X1)
    bad = ProviderResponse('{"nope": 1}', 1, 1, "end_turn")
    w4: Sequence[ProviderResponse] = [bad, bad] if failure == "invalid" else [_w4("   ")]
    provider = ScriptedProvider([*w4, V_S1])
    await _run(session_factory, settings, provider)
    assert await _variants(session_factory, setup.claim_ids[0]) == {"subclaim:s1"}
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value
    assert row.explanation == "Đúng số liệu."  # một ý: nguyên văn, không tiền tố
    # ý duy nhất là cả claim: W5 thấy đúng văn bản claim
    assert CLAIM in provider.requests[-1].messages[0]["content"]


@pytest.mark.db
async def test_v3_abstract_only_behaves_as_v1(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(
        session_factory,
        [CLAIM],
        chunks=None,
        abstract=["We present an encoder reaching 95.7%."],
        variant="V3",
        retrieved=(),
        config_extra=X1,
    )
    verdict = _verdict(evidence=[("chunk_1", "an encoder reaching 95.7%", "SUPPORTS")])
    provider = ScriptedProvider([verdict])
    await _run(session_factory, settings, provider)
    assert [r.model for r in provider.requests] == [SONNET]  # không có W4
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value
    assert row.content_level_used == ContentLevelUsed.ABSTRACT.value
    assert row.verifier == "V3"
    assert await _variants(session_factory, setup.claim_ids[0]) == set()


@pytest.mark.db
async def test_v3_subclaim_without_hits_is_inconclusive_without_llm(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, [CLAIM], variant="V3", retrieved=(), config_extra=X1)
    provider = ScriptedProvider(
        [
            _w4("zebra quantum"),
        ]
    )
    await _run(session_factory, settings, provider)
    assert len(provider.requests) == 1  # chỉ W4
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.UNVERIFIABLE.value
    assert row.unverifiable_reason == UnverifiableReason.EVIDENCE_INCONCLUSIVE.value
    assert row.needs_user_review is True


@pytest.mark.db
async def test_v3_cost_cap_applies_to_each_llm_call(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    """Hạn mức chạm sau W4: W5 không được gọi và cặp thành PROCESSING_ERROR."""
    setup = await _setup(
        session_factory,
        [CLAIM],
        variant="V3",
        retrieved=(),
        config_extra=X1,
        limits={"max_cost_usd": 0.000001},
    )
    provider = ScriptedProvider([W4_TWO, V_S1, V_S2])
    await _run(session_factory, settings, provider)
    assert [r.model for r in provider.requests] == [HAIKU]
    rows = await _by_claim(session_factory, setup.run_id)
    assert rows[setup.claim_ids[0]].unverifiable_reason == UnverifiableReason.PROCESSING_ERROR.value


def _w3(text_en: str) -> ProviderResponse:
    return ProviderResponse(json.dumps({"text_en": text_en, "kept_terms": []}), 10, 5, "end_turn")


@pytest.mark.db
async def test_w4_fallback_keeps_claim_longer_than_300_chars(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    long_claim = "Encoder accuracy reaches 95.7% " + "and further " * 30 + "on the benchmark."
    assert len(long_claim) > 300
    setup = await _setup(session_factory, [long_claim], variant="V3", retrieved=(), config_extra=X1)
    too_long = _w4(long_claim)  # one sub-claim > 300 chars fails the schema twice
    provider = ScriptedProvider([too_long, too_long, V_S1])
    await _run(session_factory, settings, provider)
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value
    assert long_claim in provider.requests[-1].messages[0]["content"]


@pytest.mark.db
async def test_x2_translates_subclaims_and_finds_evidence_via_translation(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    claim = "Các kết nối phần dư giúp ổn định mô hình sâu"
    sub = "kết nối phần dư giúp ổn định mô hình sâu"
    setup = await _setup(session_factory, [claim], variant="V3", retrieved=())
    provider = ScriptedProvider([_w4(sub), _w3("Residual connections stabilise deep models"), V_S2])
    await _run(session_factory, settings, provider)
    assert [r.model for r in provider.requests] == [HAIKU, HAIKU, SONNET]
    assert await _variants(session_factory, setup.claim_ids[0]) == {
        "subclaim:s1:translated"
    }  # bản gốc tiếng Việt không khớp chunk tiếng Anh
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value
    [ev] = await _evidence(session_factory, row.id)
    assert ev.source_chunk_id == setup.chunk_ids[2]


@pytest.mark.db
async def test_x2_writes_both_variants_when_both_match(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, [CLAIM], variant="V3", retrieved=())
    provider = ScriptedProvider(
        [_w4("encoder accuracy 95.7%"), _w3("The encoder accuracy is 95.7%"), V_S1]
    )
    await _run(session_factory, settings, provider)
    assert await _variants(session_factory, setup.claim_ids[0]) == {
        "subclaim:s1",
        "subclaim:s1:translated",
    }


@pytest.mark.db
@pytest.mark.parametrize("mode", ["x1", "english"])
async def test_no_w3_call_for_x1_or_english_claim(
    session_factory: SessionFactory, clean_db: None, settings: Settings, mode: str
) -> None:
    setup = await _setup(
        session_factory,
        [CLAIM],
        variant="V3",
        retrieved=(),
        config_extra=X1 if mode == "x1" else None,
    )
    if mode == "english":
        async with session_factory() as s:
            await s.execute(
                update(Claim).where(Claim.id == setup.claim_ids[0]).values(language="en")
            )
            await s.commit()
    provider = ScriptedProvider([_w4("encoder accuracy 95.7%"), V_S1])
    await _run(session_factory, settings, provider)
    assert [r.model for r in provider.requests] == [HAIKU, SONNET]
    assert await _variants(session_factory, setup.claim_ids[0]) == {"subclaim:s1"}
