"""Chạy đánh giá trên DB riêng và ghi kết quả (spec §11; 07a TK-15, TK-19; T-506).

`run_eval` (D1-D4) và `run_reference_eval` (D5) chỉ chạy trên DB có tên kết thúc `_eval` hoặc
`_test` (`Settings.eval_database_url`). Dữ liệu được nhập thành dòng thật (`importer`), rồi các
stage sản phẩm chạy qua `build_registry` và `work_once` như worker thật: không có đường mã riêng
cho đánh giá. Cache LLM dùng namespace `eval:<tên>@<12 ký tự đầu của hash>` (không gắn tài liệu)
và metadata cache không có TTL, để kết quả không đổi theo đồng hồ.

Chế độ `REPLAY_ONLY` không bao giờ gọi provider: thiếu entry cache là `CacheMissError`, stage ném
lại, lần chạy FAILED và harness báo `EvalRunFailedError` (CLI thoát mã 1). Kết quả ghi vào
`<out>/<YYYY-MM-DD>-<cấu hình>/`: `report.json`, `report.md`, `config.yaml`, `manifest.json`, và
`dump.sql.gz` khi `dump`. Báo cáo chỉ chứa id mục, nhãn và số liệu, không chứa văn bản.
"""

import asyncio
import gzip
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import yaml
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.enums import ClaimStatus, JobStatus, ReferenceStatus, RunStatus, StageName
from citeproof.core.errors import CiteProofError, InputError, InternalError
from citeproof.core.settings import Settings
from citeproof.db.models import (
    ClaimVerification,
    Job,
    LLMCall,
    ReferenceCheck,
    ReferenceEntry,
    RetrievalResult,
    SourceChunk,
    VerificationRun,
)
from citeproof.db.session import make_engine, make_session_factory
from citeproof.evaluation.dataset import (
    ClaimItem,
    Dataset,
    ReferenceItem,
    load_claims,
    load_references,
    load_run_config,
)
from citeproof.evaluation.importer import (
    EVAL_USER_EMAIL,
    import_claims,
    import_references,
)
from citeproof.evaluation.metrics import (
    bootstrap_ci,
    confusion,
    macro_f1,
    mrr,
    per_class_f1,
    percentile,
    recall_at_k,
)
from citeproof.llm.cache import eval_namespace
from citeproof.llm.providers.anthropic import AnthropicProvider
from citeproof.llm.providers.base import LLMProvider
from citeproof.llm.types import CacheMode, ProviderRequest, ProviderResponse
from citeproof.modules.verification.quotes import locate_quote
from citeproof.modules.verification.verifiers.base import NO_MODEL
from citeproof.pipeline.config import RunConfig
from citeproof.pipeline.deps import open_deps
from citeproof.pipeline.jobs import enqueue
from citeproof.pipeline.registry import build_registry
from citeproof.pipeline.stages import StageRegistry
from citeproof.pipeline.worker import work_once

__all__ = [
    "EVAL_USER_EMAIL",
    "EvalReport",
    "EvalRunFailedError",
    "RefEvalReport",
    "check_eval_database",
    "run_eval",
    "run_reference_eval",
]

EVAL_DB_SUFFIXES = ("_eval", "_test")
WORKER_ID = "eval"
QUOTE_NOT_VERBATIM = "QUOTE_NOT_VERBATIM"
RECALL_KS = (1, 3, 5)
_TERMINAL = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_WARNINGS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
)
_PENDING = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)
# Tài liệu thật tra được (mẫu số của "tài liệu thật bị báo chưa tìm thấy", 13 §B.1).
_REAL = frozenset({ReferenceStatus.MATCHED, ReferenceStatus.METADATA_MISMATCH})
_NOT_FOUND = ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES.value
MISSING = "MISSING"


class EvalRunFailedError(CiteProofError):
    """Lần chạy đánh giá kết thúc FAILED/CANCELLED (ví dụ thiếu cache ở chế độ phát lại)."""

    code = "EVAL_RUN_FAILED"


# --- Báo cáo -------------------------------------------------------------------------------


class ClassificationMetrics(BaseModel):
    n: int
    labels: list[str]
    accuracy: float
    macro_f1: float
    macro_f1_ci: tuple[float, float]
    per_class_f1: dict[str, float]
    confusion: list[list[int]]


class RetrievalMetrics(BaseModel):
    n: int
    recall_at_k: dict[str, float]
    mrr: float


class SystemMetrics(BaseModel):
    llm_calls: int
    cache_hits: int
    cost_usd: float
    cost_per_claim_usd: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None


class ClaimPrediction(BaseModel):
    item_id: str
    gold: str
    predicted: str
    unverifiable_reason: str | None
    downgraded_by_rule: str | None


class EvalReport(BaseModel):
    dataset: str
    dataset_sha256: str
    config: str
    variant: str
    cross_lingual: str
    mode: CacheMode
    split: str | None
    cache_namespace: str
    run_id: UUID
    run_status: RunStatus
    metrics: ClassificationMetrics
    retrieval: RetrievalMetrics | None
    quote_drop_rate: float
    system: SystemMetrics
    predictions: list[ClaimPrediction]


class ReferencePrediction(BaseModel):
    item_id: str
    expected: str
    predicted: str


class RefEvalReport(BaseModel):
    dataset: str
    dataset_sha256: str
    mode: CacheMode
    cache_namespace: str
    run_id: UUID
    run_status: RunStatus
    n: int
    labels: list[str]
    accuracy: float
    false_not_found_rate: float | None
    fabricated_detection_rate: float | None
    confusion: list[list[int]]
    system: SystemMetrics
    predictions: list[ReferencePrediction]


# --- Kiểm tra trước khi chạy ---------------------------------------------------------------


def check_eval_database(url: str) -> str:
    """Trả `url` nếu tên DB kết thúc `_eval`/`_test`; ngược lại `NOT_AN_EVAL_DATABASE`."""
    name = make_url(url).database or ""
    if not name.endswith(EVAL_DB_SUFFIXES):
        raise InputError(
            "Chỉ chạy đánh giá trên database có tên kết thúc bằng '_eval' hoặc '_test'.",
            code="NOT_AN_EVAL_DATABASE",
        )
    return url


class ReplayOnlyProvider:
    """Provider của chế độ phát lại khi không truyền provider: cùng tên với provider Anthropic
    (cùng khóa cache) nhưng không bao giờ gọi mạng. `LLMClient` ở `REPLAY_ONLY` không gọi provider
    (thiếu cache → `CacheMissError`), nên tới được đây là lỗi lập trình."""

    name = AnthropicProvider.name

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise InternalError("Chế độ phát lại không được gọi provider LLM")


def _provider_for(
    settings: Settings, mode: CacheMode, provider: LLMProvider | None
) -> LLMProvider | None:
    if provider is not None:
        return provider
    if mode is CacheMode.REPLAY_ONLY:
        return ReplayOnlyProvider()
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value():
        raise InputError(
            "Đánh giá ở chế độ read_write/fresh cần CITEPROOF_ANTHROPIC_API_KEY "
            "(chế độ replay dùng cache của một lần read_write trước).",
            code="LLM_NOT_CONFIGURED",
        )
    return None  # `make_llm_client` dựng provider Anthropic từ khóa


def _require_pg_dump() -> str:
    path = shutil.which("pg_dump")
    if path is None:
        raise InputError(
            "--dump cần lệnh pg_dump trên PATH (cài postgresql-client).", code="PG_DUMP_NOT_FOUND"
        )
    return path


# --- Chạy pipeline ---------------------------------------------------------------------------


async def _run_status(session_factory: async_sessionmaker[AsyncSession], run_id: UUID) -> str:
    async with session_factory() as session:
        status: str = (
            await session.execute(
                select(VerificationRun.status).where(VerificationRun.id == run_id)
            )
        ).scalar_one()
    return status


async def _finalize_if_idle(
    session_factory: async_sessionmaker[AsyncSession], run_id: UUID
) -> bool:
    """Lần chạy D5 dừng sau S3 (S4-S8 bỏ qua): khi không còn job chờ thì đưa S9 vào hàng đợi."""
    async with session_factory() as session:
        pending = await session.scalar(
            select(func.count()).where(Job.run_id == run_id, Job.status.in_(_PENDING))
        )
        if pending:
            return False
        added = await enqueue(session, run_id, StageName.S9)
        await session.commit()
    return added


async def _drive(
    session_factory: async_sessionmaker[AsyncSession],
    registry: StageRegistry,
    settings: Settings,
    run_id: UUID,
    *,
    finalize_when_idle: bool = False,
) -> RunStatus:
    """Chạy job bằng `work_once` (như worker) tới khi lần chạy kết thúc."""
    while True:
        worked = await work_once(session_factory, registry, WORKER_ID, settings)
        status = await _run_status(session_factory, run_id)
        if status in _TERMINAL:
            return RunStatus(status)
        if worked:
            continue
        if finalize_when_idle and await _finalize_if_idle(session_factory, run_id):
            continue
        # Job đang chờ thử lại (backoff): đợi tới lượt.
        await asyncio.sleep(settings.worker_poll_interval_seconds)


async def _failed(session_factory: async_sessionmaker[AsyncSession], run_id: UUID) -> Exception:
    async with session_factory() as session:
        run = await session.get(VerificationRun, run_id)
    error = (run.error if run is not None else None) or {}
    return EvalRunFailedError(
        f"Lần chạy đánh giá {run_id} thất bại ở {error.get('stage', '?')}: "
        f"{error.get('type', 'unknown')}.",
    )


# --- Số liệu từ DB ---------------------------------------------------------------------------


async def _system(session: AsyncSession, run_id: UUID, n_items: int) -> SystemMetrics:
    rows = (
        await session.execute(
            select(LLMCall.cache_hit, LLMCall.latency_ms, LLMCall.cost_estimate).where(
                LLMCall.run_id == run_id
            )
        )
    ).all()
    latencies = [float(row.latency_ms) for row in rows if not row.cache_hit]
    cost = float(sum((row.cost_estimate for row in rows), start=0))
    return SystemMetrics(
        llm_calls=len(rows),
        cache_hits=sum(1 for row in rows if row.cache_hit),
        cost_usd=cost,
        cost_per_claim_usd=cost / n_items if n_items else 0.0,
        latency_p50_ms=percentile(latencies, 0.5) if latencies else None,
        latency_p95_ms=percentile(latencies, 0.95) if latencies else None,
    )


def _labels(values: Sequence[str], order: Sequence[str]) -> list[str]:
    """Nhãn xuất hiện, theo thứ tự chuẩn; nhãn lạ (ví dụ `MISSING`) ở cuối."""
    present = set(values)
    known = [label for label in order if label in present]
    return known + sorted(present - set(order))


def _classification(
    gold: list[str], pred: list[str], order: Sequence[str]
) -> ClassificationMetrics:
    labels = _labels([*gold, *pred], order)

    def metric(g: Sequence[str], p: Sequence[str]) -> float:
        return macro_f1(g, p, labels)

    return ClassificationMetrics(
        n=len(gold),
        labels=labels,
        accuracy=sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold),
        macro_f1=metric(gold, pred),
        macro_f1_ci=bootstrap_ci(gold, pred, metric),
        per_class_f1=per_class_f1(gold, pred, labels),
        confusion=confusion(gold, pred, labels),
    )


async def _retrieval(
    session: AsyncSession,
    run_id: UUID,
    items: Sequence[ClaimItem],
    claim_ids: dict[str, UUID],
    retrieval_k: int,
) -> RetrievalMetrics | None:
    """Recall@k, MRR trên claim có `evidence_spans` và có kết quả truy xuất (13 §B.1).

    Xếp hạng như S8 đọc (`score_fused` tốt nhất qua các biến thể); chunk liên quan là chunk của
    cùng nguồn chứa nguyên văn một span sau chuẩn hóa (NFC, gộp khoảng trắng).
    """
    ranked: list[list[str]] = []
    relevant: list[list[str]] = []
    best = func.max(RetrievalResult.score_fused)
    for item in items:
        if not item.evidence_spans:
            continue
        claim_id = claim_ids[item.id]
        rows = (
            await session.execute(
                select(RetrievalResult.source_chunk_id, RetrievalResult.source_file_id)
                .join(SourceChunk, SourceChunk.id == RetrievalResult.source_chunk_id)
                .where(RetrievalResult.run_id == run_id, RetrievalResult.claim_id == claim_id)
                .group_by(
                    RetrievalResult.source_chunk_id,
                    RetrievalResult.source_file_id,
                    SourceChunk.chunk_index,
                )
                .order_by(best.desc(), SourceChunk.chunk_index)
            )
        ).all()
        if not rows:
            continue
        file_ids = {row.source_file_id for row in rows}
        chunks = (
            await session.execute(
                select(SourceChunk.id, SourceChunk.text).where(
                    SourceChunk.source_file_id.in_(file_ids)
                )
            )
        ).all()
        ranked.append([str(row.source_chunk_id) for row in rows])
        relevant.append(
            [
                str(chunk.id)
                for chunk in chunks
                if any(locate_quote(span.text, chunk.text) for span in item.evidence_spans)
            ]
        )
    if not ranked:
        return None
    ks = sorted({*RECALL_KS, retrieval_k})
    return RetrievalMetrics(
        n=len(ranked),
        recall_at_k={str(k): recall_at_k(ranked, relevant, k) for k in ks},
        mrr=mrr(ranked, relevant),
    )


async def _claim_report(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    dataset: Dataset[ClaimItem],
    items: Sequence[ClaimItem],
    claim_ids: dict[str, UUID],
    run_id: UUID,
    run_status: RunStatus,
    config: RunConfig,
    config_name: str,
    mode: CacheMode,
    split: str | None,
    namespace: str,
) -> EvalReport:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    ClaimVerification.claim_id,
                    ClaimVerification.status,
                    ClaimVerification.unverifiable_reason,
                    ClaimVerification.downgraded_by_rule,
                    ClaimVerification.model,
                ).where(
                    ClaimVerification.run_id == run_id,
                    ClaimVerification.verifier == config.variant.value,
                )
            )
        ).all()
        retrieval = await _retrieval(session, run_id, items, claim_ids, config.retrieval_k)
        system = await _system(session, run_id, len(items))
    by_claim = {row.claim_id: row for row in rows}
    predictions = []
    for item in items:
        row = by_claim.get(claim_ids[item.id])
        predictions.append(
            ClaimPrediction(
                item_id=item.id,
                gold=item.label.value,
                predicted=row.status if row is not None else MISSING,
                unverifiable_reason=row.unverifiable_reason if row is not None else None,
                downgraded_by_rule=row.downgraded_by_rule if row is not None else None,
            )
        )
    judged = [row for row in rows if row.model != NO_MODEL]
    dropped = sum(1 for row in judged if row.downgraded_by_rule == QUOTE_NOT_VERBATIM)
    return EvalReport(
        dataset=dataset.name,
        dataset_sha256=dataset.sha256,
        config=config_name,
        variant=config.variant.value,
        cross_lingual=config.cross_lingual,
        mode=mode,
        split=split,
        cache_namespace=namespace,
        run_id=run_id,
        run_status=run_status,
        metrics=_classification(
            [p.gold for p in predictions],
            [p.predicted for p in predictions],
            [status.value for status in ClaimStatus],
        ),
        retrieval=retrieval,
        quote_drop_rate=dropped / len(judged) if judged else 0.0,
        system=system,
        predictions=predictions,
    )


async def _reference_report(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    dataset: Dataset[ReferenceItem],
    run_id: UUID,
    document_id: UUID,
    run_status: RunStatus,
    mode: CacheMode,
    namespace: str,
) -> RefEvalReport:
    async with session_factory() as session:
        entries = (
            await session.execute(
                select(ReferenceEntry.id, ReferenceEntry.raw_text, ReferenceEntry.order_index)
                .where(ReferenceEntry.document_id == document_id)
                .order_by(ReferenceEntry.order_index)
            )
        ).all()
        checks: dict[UUID, str] = {
            row.reference_entry_id: row.status
            for row in await session.execute(
                select(ReferenceCheck.reference_entry_id, ReferenceCheck.status).where(
                    ReferenceCheck.run_id == run_id
                )
            )
        }
        system = await _system(session, run_id, len(dataset.items))
    by_text = {" ".join(entry.raw_text.split()): entry.id for entry in entries}
    predictions = []
    for item in dataset.items:
        entry_id = by_text.get(" ".join(item.raw_reference.split()))
        status = checks.get(entry_id) if entry_id is not None else None
        predictions.append(
            ReferencePrediction(
                item_id=item.id,
                expected=item.expected_status.value,
                predicted=status or MISSING,
            )
        )
    gold = [p.expected for p in predictions]
    pred = [p.predicted for p in predictions]
    labels = _labels([*gold, *pred], [status.value for status in ReferenceStatus])
    real = [p for p in predictions if ReferenceStatus(p.expected) in _REAL]
    fabricated = [p for p in predictions if p.expected == _NOT_FOUND]
    return RefEvalReport(
        dataset=dataset.name,
        dataset_sha256=dataset.sha256,
        mode=mode,
        cache_namespace=namespace,
        run_id=run_id,
        run_status=run_status,
        n=len(predictions),
        labels=labels,
        accuracy=sum(g == p for g, p in zip(gold, pred, strict=True)) / len(predictions),
        false_not_found_rate=(
            sum(p.predicted == _NOT_FOUND for p in real) / len(real) if real else None
        ),
        fabricated_detection_rate=(
            sum(p.predicted == _NOT_FOUND for p in fabricated) / len(fabricated)
            if fabricated
            else None
        ),
        confusion=confusion(gold, pred, labels),
        system=system,
        predictions=predictions,
    )


# --- Ghi kết quả -----------------------------------------------------------------------------


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pg_dump(pg_dump: str, url: str, target: Path) -> str:
    """`pg_dump` của DB đánh giá vào `target` (gzip); mật khẩu qua `PGPASSWORD`, không qua argv."""
    parsed = make_url(url)
    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = str(parsed.password)
    args = [pg_dump, "--no-owner", "--no-privileges", "--dbname", parsed.database or ""]
    if parsed.host:
        args += ["--host", parsed.host]
    if parsed.port:
        args += ["--port", str(parsed.port)]
    if parsed.username:
        args += ["--username", parsed.username]
    with (
        gzip.open(target, "wb") as out,
        subprocess.Popen(args, stdout=subprocess.PIPE, env=env) as process,
    ):
        stdout = process.stdout
        assert stdout is not None
        for block in iter(lambda: stdout.read(1 << 20), b""):
            out.write(block)
        code = process.wait()
    if code != 0:
        target.unlink(missing_ok=True)
        raise InputError(f"pg_dump kết thúc với mã {code}.", code="PG_DUMP_FAILED")
    return _sha256_file(target)


def _write_outputs(
    out: Path,
    *,
    report: BaseModel,
    markdown: str,
    config: RunConfig,
    manifest: dict[str, Any],
    dump: Callable[[Path], str] | None,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if dump is not None:
        manifest["dump"] = {"file": "dump.sql.gz", "sha256": dump(out / "dump.sql.gz")}
    (out / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown, encoding="utf-8")
    (out / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _manifest(
    *, dataset: Dataset[Any], config: RunConfig, mode: CacheMode, namespace: str, run_id: UUID
) -> dict[str, Any]:
    return {
        "commit": _git_commit(),
        "dataset": dataset.name,
        "dataset_sha256": dataset.sha256,
        "cache_namespace": namespace,
        "run_id": str(run_id),
        "variant": config.variant.value,
        "models": dict(config.models),
        "prompts": dict(config.prompts),
        "mode": mode.value,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _system_md(system: SystemMetrics) -> list[str]:
    return [
        "## System",
        "",
        f"- LLM calls: {system.llm_calls} (cache hits: {system.cache_hits})",
        f"- Cost: ${system.cost_usd:.4f} (${system.cost_per_claim_usd:.4f} per item)",
        f"- Latency p50/p95 (ms, cache misses): {_fmt(system.latency_p50_ms)} / "
        f"{_fmt(system.latency_p95_ms)}",
        "",
    ]


def _confusion_md(labels: list[str], matrix: list[list[int]]) -> list[str]:
    lines = ["| gold \\ predicted | " + " | ".join(labels) + " |"]
    lines.append("|---" * (len(labels) + 1) + "|")
    for label, row in zip(labels, matrix, strict=True):
        lines.append(f"| {label} | " + " | ".join(str(v) for v in row) + " |")
    return [*lines, ""]


def _claim_markdown(report: EvalReport) -> str:
    m = report.metrics
    lines = [
        f"# Evaluation — {report.dataset} / {report.config}",
        "",
        f"- Variant: {report.variant} ({report.cross_lingual}); cache mode: {report.mode.value}",
        f"- Split: {report.split or 'all'}; items: {m.n}; run: `{report.run_id}` "
        f"({report.run_status.value})",
        f"- Dataset SHA-256: `{report.dataset_sha256}`",
        "",
        "## Verdicts",
        "",
        f"- Macro-F1: {m.macro_f1:.4f} (95% bootstrap CI {m.macro_f1_ci[0]:.4f}-"
        f"{m.macro_f1_ci[1]:.4f})",
        f"- Accuracy: {m.accuracy:.4f}",
        f"- Quote drop rate: {report.quote_drop_rate:.4f}",
        "",
        "| class | F1 |",
        "|---|---|",
        *(f"| {label} | {score:.4f} |" for label, score in m.per_class_f1.items()),
        "",
        *_confusion_md(m.labels, m.confusion),
    ]
    if report.retrieval is not None:
        r = report.retrieval
        recalls = ", ".join(f"R@{k} {v:.4f}" for k, v in r.recall_at_k.items())
        lines += ["## Retrieval", "", f"- Claims: {r.n}; {recalls}; MRR {r.mrr:.4f}", ""]
    lines += _system_md(report.system)
    return "\n".join(lines)


def _reference_markdown(report: RefEvalReport) -> str:
    lines = [
        f"# Reference evaluation — {report.dataset}",
        "",
        f"- Cache mode: {report.mode.value}; items: {report.n}; run: `{report.run_id}` "
        f"({report.run_status.value})",
        f"- Dataset SHA-256: `{report.dataset_sha256}`",
        f"- Accuracy: {report.accuracy:.4f}",
        f"- Real references reported not found: {_fmt(report.false_not_found_rate)}",
        f"- Fabricated references detected: {_fmt(report.fabricated_detection_rate)}",
        "",
        *_confusion_md(report.labels, report.confusion),
        *_system_md(report.system),
    ]
    return "\n".join(lines)


def _result_dir(out_dir: Path, name: str) -> Path:
    return out_dir / f"{date.today():%Y-%m-%d}-{name}"


# --- Điểm vào ---------------------------------------------------------------------------------


async def run_eval(
    settings: Settings,
    *,
    dataset_path: Path,
    config_path: Path,
    mode: CacheMode,
    split: str | None,
    out_dir: Path,
    dump: bool,
    provider: LLMProvider | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EvalReport:
    """Đánh giá phán quyết claim (D1-D4) với một cấu hình biến thể."""
    url = check_eval_database(settings.eval_database_url)
    llm_provider = _provider_for(settings, mode, provider)
    pg_dump = _require_pg_dump() if dump else None
    dataset = load_claims(dataset_path)
    config = load_run_config(settings, config_path)
    namespace = eval_namespace(dataset.name, dataset.sha256[:12])
    engine = make_engine(url)
    session_factory = make_session_factory(engine)
    try:
        async with open_deps(
            settings,
            session_factory,
            cache_mode=mode,
            provider=llm_provider,
            cache_namespace=namespace,
            metadata_ttl=False,
            transport=transport,
        ) as deps:
            imported = await import_claims(
                session_factory, deps.storage, settings, dataset, config, split=split
            )
            status = await _drive(session_factory, build_registry(deps), settings, imported.run_id)
        if status not in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS):
            raise await _failed(session_factory, imported.run_id)
        items = [item for item in dataset.items if split is None or item.split == split]
        report = await _claim_report(
            session_factory,
            dataset=dataset,
            items=items,
            claim_ids=imported.claim_ids,
            run_id=imported.run_id,
            run_status=status,
            config=config,
            config_name=config_path.stem,
            mode=mode,
            split=split,
            namespace=namespace,
        )
    finally:
        await engine.dispose()
    manifest = _manifest(
        dataset=dataset, config=config, mode=mode, namespace=namespace, run_id=report.run_id
    )
    manifest["config"] = config_path.stem
    manifest["split"] = split
    await asyncio.to_thread(
        _write_outputs,
        _result_dir(out_dir, config_path.stem),
        report=report,
        markdown=_claim_markdown(report),
        config=config,
        manifest=manifest,
        dump=(lambda target: _pg_dump(pg_dump, url, target)) if pg_dump is not None else None,
    )
    return report


async def run_reference_eval(
    settings: Settings,
    *,
    dataset_path: Path,
    mode: CacheMode,
    out_dir: Path,
    provider: LLMProvider | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RefEvalReport:
    """Đánh giá trạng thái tài liệu tham khảo (D5): S1-S3 thật trên một tài liệu TEXT."""
    url = check_eval_database(settings.eval_database_url)
    llm_provider = _provider_for(settings, mode, provider)
    dataset = load_references(dataset_path)
    config = RunConfig.resolve(settings, variant=RunConfig().variant)
    namespace = eval_namespace(dataset.name, dataset.sha256[:12])
    engine = make_engine(url)
    session_factory = make_session_factory(engine)
    try:
        async with open_deps(
            settings,
            session_factory,
            cache_mode=mode,
            provider=llm_provider,
            cache_namespace=namespace,
            metadata_ttl=False,
            transport=transport,
        ) as deps:
            imported = await import_references(session_factory, deps.storage, dataset, config)
            status = await _drive(
                session_factory,
                build_registry(deps),
                settings,
                imported.run_id,
                finalize_when_idle=True,
            )
        if status not in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS):
            raise await _failed(session_factory, imported.run_id)
        report = await _reference_report(
            session_factory,
            dataset=dataset,
            run_id=imported.run_id,
            document_id=imported.document_id,
            run_status=status,
            mode=mode,
            namespace=namespace,
        )
    finally:
        await engine.dispose()
    manifest = _manifest(
        dataset=dataset, config=config, mode=mode, namespace=namespace, run_id=report.run_id
    )
    await asyncio.to_thread(
        _write_outputs,
        _result_dir(out_dir, f"refs-{dataset.name}"),
        report=report,
        markdown=_reference_markdown(report),
        config=config,
        manifest=manifest,
        dump=None,
    )
    return report
