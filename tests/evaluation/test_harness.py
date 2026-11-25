"""Harness đánh giá trên DB `_eval`/`_test` (spec §11, T-506; 07a TK-15, TK-19).

Không có mạng: LLM là `RoutedProvider` trả lời theo tác vụ (W3 dịch claim tiếng Việt, W5 theo
kịch bản của từng claim), API metadata của D5 là `httpx.MockTransport`, `pg_dump` là một script
giả trên `PATH`.
"""

import asyncio
import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import ClaimStatus, RunStatus
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.db.models import LLMCacheEntry, LLMCall, User, VerificationRun
from citeproof.evaluation.__main__ import main, parse_mode
from citeproof.evaluation.dataset import load_claims
from citeproof.evaluation.runner import (
    EVAL_USER_EMAIL,
    EvalReport,
    EvalRunFailedError,
    run_eval,
    run_reference_eval,
)
from citeproof.llm.types import CacheMode, ProviderRequest, ProviderResponse
from tests.pipeline.helpers import RoutedProvider, json_response

SessionFactory = async_sessionmaker[AsyncSession]

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "tests" / "fixtures" / "eval"
METADATA = REPO / "tests" / "fixtures" / "metadata"
MINI = EVAL / "mini.jsonl"
MINI_REFS = EVAL / "mini_refs.jsonl"
V2_X2 = REPO / "benchmark" / "configs" / "v2-x2.yaml"

# gold [S, P, C, U, S, S], dự đoán [S, S, C, U, S, UNVERIFIABLE] (claim 6: quote không nguyên văn
# → hạ cấp). F1: S 2/3, P 0, C 1, U 1, UNVERIFIABLE 0 → Macro-F1 = (8/3) / 5.
EXPECTED_MACRO_F1 = 8 / 15

_CLAIM = re.compile(r"<claim [^>]*>\n(.*?)\n</claim>", re.DOTALL)
_CHUNK = re.compile(r'<chunk id="(chunk_\d+)"[^>]*>\n(.*?)\n</chunk>', re.DOTALL)

# Đầu claim → (trạng thái, quan hệ của bằng chứng, quote).
_SCRIPT: dict[str, tuple[str, str, str]] = {
    "Residual connections allow": (
        "SUPPORTED",
        "SUPPORTS",
        "Residual connections allow very deep convolutional networks to be trained without "
        "degradation.",
    ),
    "A 152-layer": (
        "SUPPORTED",
        "SUPPORTS",
        "In our experiments, a 152-layer residual network reached a top-5 error of 4.5 percent "
        "on the image classification benchmark.",
    ),
    "Speed perturbation increased": (
        "CONTRADICTED",
        "CONTRADICTS",
        "Speed perturbation reduced the word error rate of the low-resource speech recogniser",
    ),
    "The speech corpus": (
        "UNSUPPORTED",
        "RELATED",
        "The corpus contains fifty hours of transcribed telephone speech.",
    ),
    "Kết nối residual": (
        "SUPPORTED",
        "SUPPORTS",
        "Residual connections allow very deep convolutional networks to be trained without "
        "degradation.",
    ),
    "Spectral masking": ("SUPPORTED", "SUPPORTS", "Spectral masking never changes error rates."),
}


def _w3(request: ProviderRequest) -> ProviderResponse:
    return json_response(
        {
            "text_en": "Residual connections allow training very deep convolutional networks "
            "without degradation.",
            "kept_terms": [],
        }
    )


def _w5(request: ProviderRequest) -> ProviderResponse:
    content = request.messages[-1]["content"]
    claim_match = _CLAIM.search(content)
    assert claim_match is not None
    claim = html.unescape(claim_match.group(1))
    status, relation, quote = next(
        value for prefix, value in _SCRIPT.items() if claim.startswith(prefix)
    )
    chunks = [(cid, html.unescape(text)) for cid, text in _CHUNK.findall(content)]
    assert chunks, "W5 không nhận chunk nào"
    chunk_id = next((cid for cid, text in chunks if quote in text), chunks[0][0])
    return json_response(
        {
            "status": status,
            "evidence": [{"chunk_id": chunk_id, "quote": quote, "relation": relation}],
            "abstain_cause": None,
            "explanation": "Scripted verdict.",
            "limitations": None,
        },
        model="claude-sonnet-5-5",
    )


def routed() -> RoutedProvider:
    return RoutedProvider({"W3": _w3, "W5": _w5})


@pytest.fixture
def eval_settings(settings: Settings, database_url: str) -> Settings:
    return settings.model_copy(update={"eval_database_url": database_url, "grobid_url": ""})


@pytest.fixture
def fake_pg_dump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`pg_dump` giả: in một dòng SQL, ghi tham số và biến PGPASSWORD đã nhận ra tệp."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "pg_dump.args"
    script = bin_dir / "pg_dump"
    script.write_text(
        f'#!/bin/sh\necho "$@" > "{log}"\necho "${{PGPASSWORD:+password-set}}" >> "{log}"\n'
        'echo "-- fake dump"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return log


async def _eval(
    settings: Settings,
    out_dir: Path,
    *,
    mode: CacheMode = CacheMode.READ_WRITE,
    provider: RoutedProvider | None = None,
    split: str | None = None,
    dump: bool = False,
) -> EvalReport:
    return await run_eval(
        settings,
        dataset_path=MINI,
        config_path=V2_X2,
        mode=mode,
        split=split,
        out_dir=out_dir,
        dump=dump,
        provider=provider or routed(),
    )


def _result_dir(out_dir: Path, name: str) -> Path:
    return out_dir / f"{date.today():%Y-%m-%d}-{name}"


async def test_harness_end_to_end_scripted(
    eval_settings: Settings,
    session_factory: SessionFactory,
    clean_db: None,
    tmp_path: Path,
    fake_pg_dump: Path,
) -> None:
    out_dir = tmp_path / "results"
    report = await _eval(eval_settings, out_dir, dump=True)

    assert report.run_status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}
    assert report.metrics.n == 6
    assert report.metrics.macro_f1 == pytest.approx(EXPECTED_MACRO_F1, abs=1e-9)
    assert report.metrics.per_class_f1[ClaimStatus.SUPPORTED.value] == pytest.approx(2 / 3)
    assert report.metrics.per_class_f1[ClaimStatus.PARTIALLY_SUPPORTED.value] == 0.0
    lower, upper = report.metrics.macro_f1_ci
    assert lower <= report.metrics.macro_f1 <= upper
    preds = {p.item_id: p for p in report.predictions}
    assert preds["mini-006"].predicted == ClaimStatus.UNVERIFIABLE.value
    assert preds["mini-006"].downgraded_by_rule == "QUOTE_NOT_VERBATIM"
    assert preds["mini-002"].predicted == ClaimStatus.SUPPORTED.value
    # F16: tỷ lệ quote bị loại, chi phí và độ trễ từ `llm_calls`.
    assert report.quote_drop_rate == pytest.approx(1 / 6)
    assert report.system.llm_calls == 7  # 6 x W5 + 1 x W3
    assert report.system.cache_hits == 0
    assert report.system.cost_usd > 0
    assert report.system.latency_p50_ms is not None
    assert report.system.latency_p95_ms is not None
    assert report.retrieval is not None
    assert report.retrieval.n == 5  # claim 4 không có evidence_spans
    assert report.retrieval.recall_at_k["1"] == 1.0
    assert report.retrieval.mrr == 1.0

    result = _result_dir(out_dir, "v2-x2")
    data = json.loads((result / "report.json").read_text(encoding="utf-8"))
    assert data["metrics"]["macro_f1"] == pytest.approx(EXPECTED_MACRO_F1, abs=1e-9)
    assert data["quote_drop_rate"] == pytest.approx(1 / 6)
    manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["commit"]
    assert manifest["dataset_sha256"] == hashlib.sha256(MINI.read_bytes()).hexdigest()
    assert manifest["mode"] == "read_write"
    assert manifest["models"]["verdict.judge"] == "claude-sonnet-5-5"
    assert manifest["prompts"]["verdict.judge"] == "v1"
    assert manifest["created_at"]
    dump = result / "dump.sql.gz"
    dump_bytes, args = await asyncio.to_thread(
        lambda: (dump.read_bytes(), fake_pg_dump.read_text(encoding="utf-8"))
    )
    assert manifest["dump"]["sha256"] == hashlib.sha256(dump_bytes).hexdigest()
    assert "password-set" in args
    assert ":citeproof@" not in args  # mật khẩu qua PGPASSWORD, không nằm trong tham số
    config = yaml.safe_load((result / "config.yaml").read_text(encoding="utf-8"))
    assert config["variant"] == "V2" and config["cross_lingual"] == "X2"
    markdown = (result / "report.md").read_text(encoding="utf-8")
    assert "Macro-F1" in markdown
    # Báo cáo không chứa văn bản claim hay nguồn.
    for text in (data, manifest):
        assert "Residual connections" not in json.dumps(text, ensure_ascii=False)
    assert "Residual connections" not in markdown

    async with session_factory() as session:
        run = await session.get(VerificationRun, report.run_id)
        assert run is not None
        user = await session.get(User, run.user_id)
    assert user is not None and user.email == EVAL_USER_EMAIL


async def test_split_filter(
    eval_settings: Settings, session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    report = await _eval(eval_settings, tmp_path / "results", split="test")
    assert report.metrics.n == 2
    assert {p.item_id for p in report.predictions} == {"mini-004", "mini-006"}


async def test_replay_mode_no_provider_calls_same_metrics(
    eval_settings: Settings, session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    first = await _eval(eval_settings, tmp_path / "first")
    silent = RoutedProvider({})  # cùng tên provider → cùng khóa cache; mọi lời gọi là lỗi
    second = await _eval(
        eval_settings, tmp_path / "second", mode=CacheMode.REPLAY_ONLY, provider=silent
    )
    assert silent.requests == []
    assert second.run_id != first.run_id
    assert second.metrics == first.metrics
    assert second.retrieval == first.retrieval
    assert second.quote_drop_rate == first.quote_drop_rate
    assert second.predictions == first.predictions
    assert second.system.cache_hits == second.system.llm_calls == 7
    first_json = json.loads(
        (_result_dir(tmp_path / "first", "v2-x2") / "report.json").read_text(encoding="utf-8")
    )
    second_json = json.loads(
        (_result_dir(tmp_path / "second", "v2-x2") / "report.json").read_text(encoding="utf-8")
    )
    assert second_json["metrics"] == first_json["metrics"]
    manifest = json.loads(
        (_result_dir(tmp_path / "second", "v2-x2") / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["mode"] == "replay_only"


async def test_replay_missing_cache_fails_loudly(
    eval_settings: Settings,
    session_factory: SessionFactory,
    clean_db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(EvalRunFailedError) as info:
        await _eval(
            eval_settings, tmp_path / "results", mode=CacheMode.REPLAY_ONLY, provider=routed()
        )
    assert info.value.code == "EVAL_RUN_FAILED"
    async with session_factory() as session:
        runs = list((await session.execute(select(VerificationRun))).scalars())
    assert [run.status for run in runs] == [RunStatus.FAILED.value]
    assert runs[0].error is not None and runs[0].error["type"] == "CacheMissError"
    assert not (_result_dir(tmp_path / "results", "v2-x2") / "report.json").exists()

    # CLI: cùng lỗi → mã thoát 1 (không có khóa API: replay không bao giờ gọi provider).
    monkeypatch.setenv("CITEPROOF_EVAL_DATABASE_URL", eval_settings.eval_database_url)
    monkeypatch.setenv("CITEPROOF_STORAGE_DIR", str(eval_settings.storage_dir))
    monkeypatch.setenv("CITEPROOF_GROBID_URL", "")
    monkeypatch.setenv("CITEPROOF_ANTHROPIC_API_KEY", "")
    argv = [
        "run",
        "--dataset",
        str(MINI),
        "--config",
        str(V2_X2),
        "--mode",
        "replay",
        "--out",
        str(tmp_path / "cli"),
    ]
    assert await asyncio.to_thread(main, argv) == 1
    async with session_factory() as session:
        statuses = list((await session.execute(select(VerificationRun.status))).scalars())
    assert statuses == [RunStatus.FAILED.value, RunStatus.FAILED.value]


async def test_eval_namespace_used(
    eval_settings: Settings, session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    report = await _eval(eval_settings, tmp_path / "results")
    sha12 = load_claims(MINI).sha256[:12]
    assert report.cache_namespace == f"eval:mini@{sha12}"
    async with session_factory() as session:
        entries = list((await session.execute(select(LLMCacheEntry))).scalars())
        calls = list((await session.execute(select(LLMCall))).scalars())
    assert entries and calls
    assert all(entry.namespace.startswith("eval:mini@") for entry in entries)
    assert {entry.namespace for entry in entries} == {report.cache_namespace}
    assert all(entry.document_id is None for entry in entries)
    assert {call.cache_namespace for call in calls} == {report.cache_namespace}


# --- D5 -------------------------------------------------------------------------------------

HE_DOI = "10.1109/cvpr.2016.90"


def _metadata(name: str) -> Any:
    return json.loads((METADATA / f"{name}.json").read_text(encoding="utf-8"))


def _metadata_api(request: httpx.Request) -> httpx.Response:
    """Crossref biết He et al. theo DOI; mọi tra cứu khác không có kết quả."""
    host = request.url.host
    path = request.url.path.lower()
    if host == "api.crossref.org":
        if path == f"/works/{HE_DOI}":
            return httpx.Response(200, json=_metadata("crossref_doi_he"))
        if path == "/works":
            return httpx.Response(200, json={"status": "ok", "message": {"items": []}})
        return httpx.Response(404)
    if host == "api.openalex.org":
        if path == "/works":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)
    if host == "api.semanticscholar.org":
        if path.startswith("/graph/v1/paper/search"):
            return httpx.Response(200, json={"total": 0, "data": []})
        return httpx.Response(404)
    raise AssertionError(f"host ngoài dự kiến: {host}")


async def test_reference_eval_accuracy(
    eval_settings: Settings, session_factory: SessionFactory, clean_db: None, tmp_path: Path
) -> None:
    out_dir = tmp_path / "results"
    report = await run_reference_eval(
        eval_settings,
        dataset_path=MINI_REFS,
        mode=CacheMode.READ_WRITE,
        out_dir=out_dir,
        provider=RoutedProvider({}),
        transport=httpx.MockTransport(_metadata_api),
    )
    assert report.run_status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}
    preds = {p.item_id: p.predicted for p in report.predictions}
    assert preds == {
        "ref-001": "MATCHED",
        "ref-002": "NOT_FOUND_IN_SEARCHED_SOURCES",
        "ref-003": "NOT_FOUND_IN_SEARCHED_SOURCES",
    }
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.false_not_found_rate == pytest.approx(1 / 2)
    assert report.fabricated_detection_rate == 1.0
    data = json.loads(
        (_result_dir(out_dir, "refs-mini_refs") / "report.json").read_text(encoding="utf-8")
    )
    assert data["accuracy"] == pytest.approx(2 / 3)
    assert data["false_not_found_rate"] == pytest.approx(1 / 2)
    manifest = json.loads(
        (_result_dir(out_dir, "refs-mini_refs") / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["mode"] == "read_write"
    assert manifest["dataset_sha256"] == hashlib.sha256(MINI_REFS.read_bytes()).hexdigest()


# --- Kiểm tra trước khi chạy (không cần DB) --------------------------------------------------


def _offline_settings(url: str, tmp_path: Path) -> Settings:
    return Settings(eval_database_url=url, storage_dir=tmp_path / "storage", anthropic_api_key=None)


def test_refuses_product_database(tmp_path: Path) -> None:
    settings = _offline_settings("postgresql+psycopg://u:p@localhost:5432/citeproof", tmp_path)
    with pytest.raises(InputError) as info:
        asyncio.run(
            run_eval(
                settings,
                dataset_path=MINI,
                config_path=V2_X2,
                mode=CacheMode.READ_WRITE,
                split=None,
                out_dir=tmp_path,
                dump=False,
                provider=routed(),
            )
        )
    assert info.value.code == "NOT_AN_EVAL_DATABASE"
    with pytest.raises(InputError) as refs:
        asyncio.run(
            run_reference_eval(
                settings,
                dataset_path=MINI_REFS,
                mode=CacheMode.READ_WRITE,
                out_dir=tmp_path,
                provider=routed(),
            )
        )
    assert refs.value.code == "NOT_AN_EVAL_DATABASE"


def test_read_write_needs_llm(tmp_path: Path) -> None:
    settings = _offline_settings("postgresql+psycopg://u:p@localhost:5432/x_eval", tmp_path)
    with pytest.raises(InputError) as info:
        asyncio.run(
            run_eval(
                settings,
                dataset_path=MINI,
                config_path=V2_X2,
                mode=CacheMode.READ_WRITE,
                split=None,
                out_dir=tmp_path,
                dump=False,
            )
        )
    assert info.value.code == "LLM_NOT_CONFIGURED"


def test_dump_needs_pg_dump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    settings = _offline_settings("postgresql+psycopg://u:p@localhost:5432/x_eval", tmp_path)
    with pytest.raises(InputError) as info:
        asyncio.run(
            run_eval(
                settings,
                dataset_path=MINI,
                config_path=V2_X2,
                mode=CacheMode.READ_WRITE,
                split=None,
                out_dir=tmp_path,
                dump=True,
                provider=routed(),
            )
        )
    assert info.value.code == "PG_DUMP_NOT_FOUND"


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        ("read_write", CacheMode.READ_WRITE),
        ("replay", CacheMode.REPLAY_ONLY),
        ("replay_only", CacheMode.REPLAY_ONLY),
        ("fresh", CacheMode.FRESH),
    ],
)
def test_cli_modes(value: str, mode: CacheMode) -> None:
    assert parse_mode(value) is mode
