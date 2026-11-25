"""A2 Evidence Investigator trong S8: V4 và V2+A2 (spec §9, `07a` §4.5.3; T-704)."""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import select

from citeproof.core.enums import (
    AgentRunStatus,
    ClaimStatus,
    ContentLevelUsed,
    UnverifiableReason,
    UserRole,
    Verifier,
)
from citeproof.core.settings import Settings
from citeproof.db.models import ClaimVerification, VerificationRun
from citeproof.db.models.agents import AgentRun
from citeproof.llm.providers.scripted import ScriptedProvider
from citeproof.llm.types import ProviderResponse, TurnResponse
from citeproof.services import auth, documents
from citeproof.services.documents import ADMIN_VARIANTS
from citeproof.storage import LocalStorage
from citeproof.web.rendering import templates
from tests.files import PDF_BYTES
from tests.modules.verification.test_verify_stage import (
    SUPPORTED,
    SessionFactory,
    _evidence,
    _rows,
    _run,
    _setup,
    _verdict,
)

MODEL = "claude-sonnet-5-5"
ENABLED: dict[str, Any] = {"agents": {"evidence_investigator": {"enabled": True}}}
QUOTE = "The encoder accuracy reaches 95.7%"
V2_EXPLANATION = "GIẢI_THÍCH_RIÊNG_CỦA_V2 không được gửi cho A2."
V2_UNSUPPORTED = _verdict(
    "UNSUPPORTED",
    [("chunk_2", "Introduction to the encoder architecture", "RELATED")],
    explanation=V2_EXPLANATION,
)


def tool_use(name: str, payload: Mapping[str, Any], block_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": dict(payload)}


def turn(*blocks: dict[str, Any]) -> TurnResponse:
    return TurnResponse(
        content=tuple(blocks),
        stop_reason="tool_use",
        input_tokens=800,
        output_tokens=60,
        model=MODEL,
    )


def outline_turn(block_id: str = "o1") -> TurnResponse:
    return turn(tool_use("source_outline", {}, block_id))


def search_turn(query: str, block_id: str = "s1") -> TurnResponse:
    return turn(tool_use("source_search", {"query": query, "k": 5, "section_id": None}, block_id))


def read_turn(*ids: str, block_id: str = "r1") -> TurnResponse:
    return turn(tool_use("source_read", {"chunk_ids": list(ids), "neighbors": 0}, block_id))


def submit_turn(
    status: str = "SUPPORTED",
    evidence: Sequence[tuple[str, str, str]] = (("chunk_2", QUOTE, "SUPPORTS"),),
    block_id: str = "v1",
    explanation: str = "Nguồn nêu đúng con số 95,7%.",
) -> TurnResponse:
    payload = {
        "schema_version": "verdict/2",
        "status": status,
        "evidence": [{"chunk_id": c, "quote": q, "relation": r} for c, q, r in evidence],
        "abstain_cause": None,
        "explanation": explanation,
        "limitations": None,
    }
    return turn(tool_use("submit_verdict", payload, block_id))


HAPPY = [outline_turn(), search_turn("encoder accuracy 95.7"), read_turn("chunk_2"), submit_turn()]


def _provider(
    turns: Sequence[TurnResponse], responses: Sequence[ProviderResponse] = ()
) -> ScriptedProvider:
    return ScriptedProvider(responses, turns=turns)


async def _agent_runs(sf: SessionFactory, run_id: uuid.UUID) -> list[AgentRun]:
    async with sf() as s:
        rows = await s.execute(
            select(AgentRun).where(AgentRun.run_id == run_id).order_by(AgentRun.started_at)
        )
        return list(rows.scalars())


def _tool_results(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    content = messages[-1]["content"]
    assert isinstance(content, list)
    return [b for b in content if b.get("type") == "tool_result"]


def _final(rows: Sequence[ClaimVerification], verifier: str) -> ClaimVerification:
    [row] = [r for r in rows if r.verifier == verifier]
    return row


# --- V4 ---------------------------------------------------------------------------------------


@pytest.mark.db
async def test_v4_writes_agent_run_and_verdict(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, variant="V4", retrieved=(), config_extra=ENABLED)
    provider = _provider(HAPPY)
    await _run(session_factory, settings, provider)
    assert provider.requests == []  # V4 không gọi W5
    [row] = await _rows(session_factory, setup.run_id)
    assert row.verifier == "V4"
    assert row.status == ClaimStatus.SUPPORTED.value
    assert row.content_level_used == ContentLevelUsed.FULL_TEXT.value
    assert (row.model, row.prompt_version, row.schema_version) == (MODEL, "v1", "verdict/2")
    assert row.needs_user_review is False
    [agent_run] = await _agent_runs(session_factory, setup.run_id)
    assert row.agent_run_id == agent_run.id
    assert agent_run.status == AgentRunStatus.SUBMITTED.value
    assert agent_run.target_id == setup.claim_ids[0]
    [ev] = await _evidence(session_factory, row.id)
    assert ev.source_chunk_id == setup.chunk_ids[1]
    assert (ev.char_start, ev.char_end) == (0, len(QUOTE))
    assert ev.quote_text == QUOTE
    first = provider.turn_requests[0]
    assert {t.name for t in first.tools} == {
        "source_outline",
        "source_search",
        "source_read",
        "submit_verdict",
    }
    assert "First-pass result:\n(none)" in first.messages[0]["content"]


@pytest.mark.db
async def test_cite_unread_chunk_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, variant="V4", retrieved=(), config_extra=ENABLED)
    residual = "Residual connections stabilise deep models."
    provider = _provider(
        [
            search_turn("residual connections"),
            submit_turn(evidence=[("chunk_3", residual, "SUPPORTS")], block_id="v1"),
            read_turn("chunk_3"),
            submit_turn(evidence=[("chunk_3", residual, "SUPPORTS")], block_id="v2"),
        ]
    )
    await _run(session_factory, settings, provider)
    [result] = _tool_results(provider.turn_requests[2].messages)
    assert result["is_error"] is True
    assert "CHUNK_NOT_READ" in result["content"]
    assert "Read chunk_3 with source_read before citing it." in result["content"]
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value
    [ev] = await _evidence(session_factory, row.id)
    assert ev.source_chunk_id == setup.chunk_ids[2]


@pytest.mark.db
async def test_quote_not_in_chunk_rejected(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, variant="V4", retrieved=(), config_extra=ENABLED)
    provider = _provider(
        [
            read_turn("chunk_2"),
            submit_turn(evidence=[("chunk_2", "accuracy is 96%", "SUPPORTS")], block_id="v1"),
            submit_turn(block_id="v2"),
        ]
    )
    await _run(session_factory, settings, provider)
    [result] = _tool_results(provider.turn_requests[2].messages)
    assert result["is_error"] is True
    assert "QUOTE_NOT_IN_CHUNK" in result["content"]
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.SUPPORTED.value


@pytest.mark.db
async def test_v4_unread_citation_after_attempts_downgraded(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    """Sau hai lần nộp sai runner nhận bản cuối; apply_rules chỉ coi chunk đã đọc là hợp lệ."""
    setup = await _setup(session_factory, variant="V4", retrieved=(), config_extra=ENABLED)
    provider = _provider([submit_turn(block_id="v1"), submit_turn(block_id="v2")])
    await _run(session_factory, settings, provider)
    [row] = await _rows(session_factory, setup.run_id)
    assert row.status == ClaimStatus.UNVERIFIABLE.value
    assert row.unverifiable_reason == UnverifiableReason.EVIDENCE_INCONCLUSIVE.value
    assert row.downgraded_by_rule == "QUOTE_NOT_VERBATIM"
    assert row.needs_user_review is True
    assert row.agent_run_id is not None


@pytest.mark.db
async def test_v4_budget_exhausted_processing_error(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    config = {"agents": {"evidence_investigator": {"enabled": True, "max_steps": 1}}}
    setup = await _setup(session_factory, variant="V4", retrieved=(), config_extra=config)
    provider = _provider([search_turn("encoder"), search_turn("accuracy", block_id="s2")])
    await _run(session_factory, settings, provider)
    [row] = await _rows(session_factory, setup.run_id)
    assert row.verifier == "V4"
    assert row.status == ClaimStatus.UNVERIFIABLE.value
    assert row.unverifiable_reason == UnverifiableReason.PROCESSING_ERROR.value
    assert row.needs_user_review is True
    [agent_run] = await _agent_runs(session_factory, setup.run_id)
    assert agent_run.status == AgentRunStatus.BUDGET_EXHAUSTED.value
    assert row.agent_run_id == agent_run.id


@pytest.mark.db
async def test_v4_cost_gate_stops_agent(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    """Hạn mức chi phí: cặp đầu chạy A2 (llm_calls theo bước), cặp sau không bắt đầu A2."""
    setup = await _setup(
        session_factory,
        ["Encoder đạt 95,7%", "Encoder đạt 95,7% lần nữa"],
        variant="V4",
        retrieved=(),
        config_extra=ENABLED,
        limits={"max_cost_usd": 0.000001},
    )
    provider = _provider(HAPPY)
    await _run(session_factory, settings.model_copy(update={"llm_concurrency": 1}), provider)
    assert len(await _agent_runs(session_factory, setup.run_id)) == 1
    rows = {r.claim_id: r for r in await _rows(session_factory, setup.run_id)}
    assert rows[setup.claim_ids[0]].status == ClaimStatus.SUPPORTED.value
    second = rows[setup.claim_ids[1]]
    assert second.unverifiable_reason == UnverifiableReason.PROCESSING_ERROR.value
    assert second.agent_run_id is None


@pytest.mark.db
async def test_v4_abstract_only_uses_v1_path(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(
        session_factory,
        chunks=None,
        abstract=["We present an encoder reaching 95.7%."],
        variant="V4",
        retrieved=(),
        config_extra=ENABLED,
    )
    verdict = _verdict(evidence=[("chunk_1", "an encoder reaching 95.7%", "SUPPORTS")])
    provider = _provider([], [verdict])
    await _run(session_factory, settings, provider)
    assert provider.turn_requests == []
    [row] = await _rows(session_factory, setup.run_id)
    assert row.verifier == "V4"
    assert row.content_level_used == ContentLevelUsed.ABSTRACT.value
    assert row.agent_run_id is None
    assert await _agent_runs(session_factory, setup.run_id) == []


# --- V2+A2 ------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("first", ["SUPPORTED", "UNSUPPORTED", "PARTIALLY_SUPPORTED"])
async def test_v2a2_escalates_only_unsupported_partial_fulltext(
    session_factory: SessionFactory, clean_db: None, settings: Settings, first: str
) -> None:
    setup = await _setup(session_factory, variant="V2+A2", config_extra=ENABLED)
    w5 = {
        "SUPPORTED": SUPPORTED,
        "UNSUPPORTED": V2_UNSUPPORTED,
        "PARTIALLY_SUPPORTED": _verdict(
            "PARTIALLY_SUPPORTED", [("chunk_1", QUOTE, "PARTIALLY_SUPPORTS")]
        ),
    }[first]
    turns = HAPPY if first != "SUPPORTED" else []
    provider = _provider(turns, [w5])
    await _run(session_factory, settings, provider)
    agent_runs = await _agent_runs(session_factory, setup.run_id)
    rows = await _rows(session_factory, setup.run_id)
    assert sorted(r.verifier for r in rows) == ["V2", "V2+A2"]
    final = _final(rows, "V2+A2")
    if first == "SUPPORTED":
        assert agent_runs == []
        assert final.agent_run_id is None
        assert final.escalated_from is None
        assert final.status == ClaimStatus.SUPPORTED.value
        assert len(await _evidence(session_factory, final.id)) == 1
    else:
        assert len(agent_runs) == 1
        assert final.agent_run_id == agent_runs[0].id
        assert final.status == ClaimStatus.SUPPORTED.value


@pytest.mark.db
async def test_v2a2_abstract_only_never_escalates(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(
        session_factory,
        chunks=None,
        abstract=["We present an encoder reaching 95.7%."],
        variant="V2+A2",
        config_extra=ENABLED,
    )
    partial = _verdict(
        "PARTIALLY_SUPPORTED", [("chunk_1", "an encoder reaching 95.7%", "PARTIALLY_SUPPORTS")]
    )
    provider = _provider([], [partial])
    await _run(session_factory, settings, provider)
    assert await _agent_runs(session_factory, setup.run_id) == []
    final = _final(await _rows(session_factory, setup.run_id), "V2+A2")
    assert final.content_level_used == ContentLevelUsed.ABSTRACT.value


@pytest.mark.db
async def test_v2a2_writes_v2_and_final_rows(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, variant="V2+A2", config_extra=ENABLED)
    provider = _provider(HAPPY, [V2_UNSUPPORTED])
    await _run(session_factory, settings, provider)
    rows = await _rows(session_factory, setup.run_id)
    v2 = _final(rows, "V2")
    final = _final(rows, "V2+A2")
    assert v2.status == ClaimStatus.UNSUPPORTED.value
    assert v2.needs_user_review is True  # TK-13
    assert v2.agent_run_id is None
    assert final.escalated_from == v2.id
    assert final.status == ClaimStatus.SUPPORTED.value
    assert final.needs_user_review is False
    [ev] = await _evidence(session_factory, final.id)
    assert ev.source_chunk_id == setup.chunk_ids[1]


@pytest.mark.db
async def test_a2_gets_status_not_explanation(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    await _setup(session_factory, variant="V2+A2", config_extra=ENABLED)
    provider = _provider(HAPPY, [V2_UNSUPPORTED])
    await _run(session_factory, settings, provider)
    user = provider.turn_requests[0].messages[0]["content"]
    assert "UNSUPPORTED" in user
    # Bằng chứng của V2 (chunk thứ nhất của nguồn) theo ID của phiên A2.
    assert '"evidence_chunk_ids": ["chunk_1"]' in user
    assert V2_EXPLANATION not in user
    assert "GIẢI_THÍCH" not in provider.turn_requests[0].system


@pytest.mark.db
async def test_v2a2_agent_failure_keeps_v2_flagged(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    config = {"agents": {"evidence_investigator": {"enabled": True, "max_steps": 1}}}
    setup = await _setup(session_factory, variant="V2+A2", config_extra=config)
    provider = _provider(
        [search_turn("encoder"), search_turn("accuracy", block_id="s2")], [V2_UNSUPPORTED]
    )
    await _run(session_factory, settings, provider)
    rows = await _rows(session_factory, setup.run_id)
    v2 = _final(rows, "V2")
    final = _final(rows, "V2+A2")
    assert final.status == v2.status == ClaimStatus.UNSUPPORTED.value
    assert final.explanation == v2.explanation
    assert final.needs_user_review is True
    [agent_run] = await _agent_runs(session_factory, setup.run_id)
    assert final.agent_run_id == agent_run.id
    assert final.escalated_from == v2.id
    assert len(await _evidence(session_factory, final.id)) == 1


@pytest.mark.db
async def test_v2a2_agent_disabled_copies_v2(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    setup = await _setup(session_factory, variant="V2+A2")
    provider = _provider([], [V2_UNSUPPORTED])
    await _run(session_factory, settings, provider)
    assert await _agent_runs(session_factory, setup.run_id) == []
    final = _final(await _rows(session_factory, setup.run_id), "V2+A2")
    assert final.status == ClaimStatus.UNSUPPORTED.value
    assert final.needs_user_review is True


# --- Admin ------------------------------------------------------------------------------------


@pytest.mark.db
async def test_admin_can_choose_v4_and_v2a2(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    assert Verifier.V4 in ADMIN_VARIANTS and Verifier.V2_A2 in ADMIN_VARIANTS
    storage = LocalStorage(settings.storage_dir)
    async with session_factory() as s:
        user = await auth.create_user(
            s, email="u@b.vn", password="mat-khau-du-dai", display_name="U"
        )
        admin = await auth.create_user(
            s, email="ad@b.vn", password="mat-khau-du-dai", display_name="A", role=UserRole.ADMIN
        )
        await s.commit()
        made: dict[tuple[str, Verifier], uuid.UUID] = {}
        for who, person in (("user", user), ("admin", admin)):
            for variant in (Verifier.V4, Verifier.V2_A2):
                _, run_id = await documents.create_file_document(
                    s,
                    storage,
                    settings,
                    user=person,
                    filename="a.pdf",
                    data=PDF_BYTES,
                    variant=variant,
                )
                made[(who, variant)] = run_id
        runs = {r.id: r.config for r in (await s.execute(select(VerificationRun))).scalars()}
    for variant in (Verifier.V4, Verifier.V2_A2):
        assert runs[made[("user", variant)]]["variant"] == "V2"
        config = runs[made[("admin", variant)]]
        assert config["variant"] == variant.value
        assert config["agents"]["evidence_investigator"]["enabled"] is True
        assert config["agents"]["reference_resolver"]["enabled"] is False
    # Ô chọn biến thể khi kiểm tra lại dùng cùng danh sách.
    assert templates.env.globals["admin_variants"] == [v.value for v in ADMIN_VARIANTS]
    source = templates.env.loader.get_source(templates.env, "partials/report_head.html")[0]
    assert "admin_variants" in source and "'V3'" not in source
