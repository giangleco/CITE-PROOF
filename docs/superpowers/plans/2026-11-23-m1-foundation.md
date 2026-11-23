# M1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the CiteProof M1 foundation. That means a Python repo with tooling, Docker services, the full DB schema (08 + 07a memory tables), a PostgreSQL job queue and pipeline runner that drives an empty S1–S9 run end to end with resume, an `LLMClient` with namespaced cache, record/replay and call logging, prompt/context primitives, an SSRF-safe HTTP client, and CI.

**Architecture:** This is a modular monolith (`src/citeproof/`) following docs/12 §4.1. All state lives in PostgreSQL: domain tables, jobs, LLM cache, and agent/retrieval tables. A worker claims stage jobs with `FOR UPDATE SKIP LOCKED` and advances a fixed DAG of stages. In M1 every stage is a no-op. LLM access goes only through `LLMClient` over a provider protocol. M1 ships only a scripted provider; the real provider waits for ADR-006.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2 (async) + psycopg 3, Alembic, pgvector, httpx, Jinja2, pytest + pytest-asyncio, Ruff, mypy, Docker Compose (`pgvector/pgvector:pg17`, `lfoppiano/grobid:0.8.1`), GitHub Actions.

**Spec:** `docs/07a-orchestration-memory-tools.md` (approved, D-13). Also read `docs/08-database-design.md`, `docs/09-api-interface-specification.md` §4.4–§4.6, `docs/12-development-guidelines.md`, and `docs/13-testing-strategy.md` §A. Tasks map to T-101…T-109 in `docs/task-tracking.md`.

**Out of scope for M1:**
- A real LLM provider adapter. ADR-006 and Q-03 are still open.
- Object storage (T-202).
- `AgentRunner` and tools (T-406).
- Any real stage logic.

## Global Constraints

- Python `>=3.12`; `.python-version` = `3.12`; Docker base `python:3.12-slim`.
- Code identifiers in English. User-facing text and docs in Vietnamese (12 §4.2).
- Ruff for lint and format. mypy `strict = true` for the whole `citeproof` package, with the pydantic plugin.
- Pydantic at every boundary: API, LLM output, between stages (12 §4.2).
- Logging: JSON. Required fields are `timestamp`, `level`, `module`, `event`, plus `run_id` / `document_id` when bound. Never log document text, source content, full prompts, passwords or tokens (12 §4.6).
- Config only via env vars read by pydantic-settings, prefix `CITEPROOF_`. `.env.example` lists every variable without secret values (12 §4.5).
- Commits follow Conventional Commits with the task ID, e.g. `feat(llm): add namespaced cache (T-107)` (12 §4.3). Work on branch `feat/m1-foundation`, created from `docs/orchestration-memory-tools`. Never commit to `main`.
- Tests that call a real LLM or real external APIs never run in default CI (12 §4.10).
- Enum values are copied exactly from `08` / `04` §4.5. They are stored as `TEXT` + `CHECK` constraints, not PG enums (08 §8).
- `ReferenceStatus` includes `LOOKUP_FAILED` (D-12). `UnverifiableReason` includes `EVIDENCE_INCONCLUSIVE` (D-11). Evidence relations are exactly `SUPPORTS`, `PARTIALLY_SUPPORTS`, `CONTRADICTS`, `RELATED`.
- Cache namespaces are exactly `doc:<document_id>` and `eval:<dataset>@<hash>` (07a §4.7.2).
- No sampling parameters (temperature/top_p/top_k) and no forced tool choice anywhere in LLM request types (12 §4.8 rule 8).
- `metadata_cache` stores a hash of the query, never the query text (07a §4.7.3).
- Every URL derived from user data is fetched only through `integrations/http_safe` (12 §4.8 rule 12).

## Review Focus

1. **S3 and S4 finish at the same moment on two workers.** S5 must be enqueued exactly once and the run must still complete. Pinned in Task 7 (`test_concurrent_workers_enqueue_s5_once`).
2. **A worker dies mid-job.** The job stays `RUNNING` forever unless it is reclaimed after the lock timeout. Pinned in Task 6 (`test_stale_running_job_is_reclaimed`).
3. **Vietnamese text in NFD vs NFC form** (e.g. "Nguyễn" typed on different keyboards). It must produce the same cache key. Pinned in Task 10 (`test_nfc_and_nfd_inputs_share_cache_key`).
4. **Source text containing `</source_chunks>` or a fake `<chunk id=…>`.** It must not escape the data block. Pinned in Task 9 (`test_data_block_cannot_be_closed_from_inside`).
5. **SSRF bypass forms.** These include a redirect to `127.0.0.1`, a host resolving to mixed public and private IPs, `::ffff:127.0.0.1`, and the decimal host `2130706433`. All must be blocked. Pinned in Task 12 (`test_bypass_forms_are_blocked`).

---

### Task 1: Repository scaffold and tooling (T-101)

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `Makefile`, `src/citeproof/__init__.py`, `src/citeproof/py.typed`, `tests/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: package `citeproof` (src layout) with `citeproof.__version__: str = "0.1.0"`; `make check`, `make test`, `make fmt`.

- [ ] **Step 1: Create the project with uv**
  Run `uv init --package --name citeproof --python 3.12`, then reshape to the src layout above. Add runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy[asyncio]>=2`, `psycopg[binary]>=3.2`, `alembic`, `pgvector`, `httpx`, `jinja2`. Add dev group deps: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `pre-commit`.
  Configure in `pyproject.toml`:
  - Ruff: `line-length = 100`, `target-version = "py312"`, lint `select = ["E","F","I","B","UP","ASYNC","SIM","RUF"]`.
  - mypy: `strict = true`, `plugins = ["pydantic.mypy"]`, `packages = ["citeproof"]`.
  - pytest: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, marker `db: needs PostgreSQL`.

- [ ] **Step 2: Write the smoke test**

```python
def test_package_imports_with_version():
    import citeproof
    assert citeproof.__version__ == "0.1.0"
```

- [ ] **Step 3: Write the Makefile**
  - `check`: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
  - `test`: `uv run pytest`
  - `fmt`: `uv run ruff format . && uv run ruff check --fix .`
  - `up`: `docker compose up -d`
  - `migrate`: `uv run alembic upgrade head`

- [ ] **Step 4: Verify**
  Run `make check && make test`. Expected: ruff and mypy clean; `1 passed`.

- [ ] **Step 5: Commit** `chore: scaffold citeproof package and tooling (T-101)`

---

### Task 2: Settings, JSON logging, error hierarchy (T-103)

**Files:**
- Create: `src/citeproof/core/__init__.py`, `src/citeproof/core/settings.py`, `src/citeproof/core/logging.py`, `src/citeproof/core/errors.py`, `.env.example`
- Test: `tests/core/test_settings.py`, `tests/core/test_logging.py`

**Interfaces:**
- Produces:
  - `Settings(BaseSettings)` with `env_prefix="CITEPROOF_"` and these fields and defaults:
    - `env: Literal["local","test","pilot"] = "local"`
    - `database_url: str = "postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof"`
    - `eval_database_url: str = "postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof_eval"`
    - `log_level: str = "INFO"`
    - `job_max_attempts: int = 3`
    - `job_backoff_base_seconds: float = 5.0`
    - `job_lock_timeout_seconds: int = 900`
    - `worker_poll_interval_seconds: float = 1.0`
    - `llm_prices: dict[str, tuple[float, float]] = {}` (USD per 1M input/output tokens, keyed by model)
  - `get_settings() -> Settings` (cached).
  - `configure_logging(level: str) -> None`; `log_context(**fields: str) -> ContextManager[None]`; `ALLOWED_LOG_FIELDS: frozenset[str] = {"run_id","document_id","job_id","stage","worker_id","task","model","status","duration_ms","error_type"}`.
  - Errors exactly as 12 §4.7: `CiteProofError`, `InputError`, `ParseError`, `ExternalServiceError`, `RetryableError(ExternalServiceError)`, `LLMOutputError`, `InternalError`.

- [ ] **Step 1: Write failing tests**

```python
def test_settings_read_prefixed_env(monkeypatch):
    monkeypatch.setenv("CITEPROOF_JOB_MAX_ATTEMPTS", "5")
    assert Settings().job_max_attempts == 5

def test_log_line_is_json_with_required_fields(capsys):
    configure_logging("INFO")
    with log_context(run_id="r1"):
        logging.getLogger("citeproof.test").info("stage_started")
    rec = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert rec["event"] == "stage_started" and rec["run_id"] == "r1"
    assert {"timestamp", "level", "module"} <= rec.keys()

def test_non_allowlisted_extra_is_dropped(capsys):
    configure_logging("INFO")
    logging.getLogger("citeproof.test").info("x", extra={"text": "bản thảo bí mật", "stage": "S1"})
    rec = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "text" not in rec and rec["stage"] == "S1"
```

- [ ] **Step 2: Run tests.** `uv run pytest tests/core -v` → FAIL (import errors).
- [ ] **Step 3: Implement**
  - `JsonFormatter` writes to stderr.
  - `timestamp` is ISO-8601 UTC.
  - Context fields are held in a `contextvars.ContextVar[dict[str,str]]`.
  - Only keys in `ALLOWED_LOG_FIELDS` pass through from `extra` or context.
- [ ] **Step 4: Write `.env.example`** with every `Settings` field, using the default values shown.
- [ ] **Step 5: Run** `uv run pytest tests/core -v && make check` → PASS.
- [ ] **Step 6: Commit** `feat(core): add settings, JSON logging and error hierarchy (T-103)`

---

### Task 3: DB infrastructure, enums, Alembic, test fixtures (T-102, T-104)

**Files:**
- Create: `docker-compose.yml` (services `db`, `grobid` only for now), `docker/db/init/01-databases.sql`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_extensions.py`, `src/citeproof/core/enums.py`, `src/citeproof/db/__init__.py`, `src/citeproof/db/base.py`, `src/citeproof/db/session.py`, `tests/conftest.py`
- Test: `tests/core/test_enums.py`, `tests/db/test_migrations.py`

**Interfaces:**
- Produces:
  - `core/enums.py`: `StrEnum` classes with values copied exactly from 08 §4.2 / 04 §4.5 / 07a §4.7.6:
    - `UserRole`, `DocumentKind`, `Language`, `DomainFlag`, `RefType`, `ParseMethod`
    - `ReferenceStatus` (… `PARSE_FAILED`, `LOOKUP_FAILED`), `ResolvedBy`
    - `SourceOrigin`, `ContentLevel` (`ABSTRACT`, `FULL_TEXT`), `ContentLevelUsed` (`NONE`, `ABSTRACT`, `FULL_TEXT`), `TitleMatch`, `ChunkRegion`, `LinkMethod`
    - `RunStatus`, `StageName` (`S1`…`S9`), `ClaimStatus`, `UnverifiableReason` (… `PROCESSING_ERROR`, `EVIDENCE_INCONCLUSIVE`), `EvidenceRelation`
    - `FeedbackTarget`, `FeedbackVerdict`, `JobStatus`
    - `AgentName`, `AgentTargetType`, `AgentRunStatus`
    - `Verifier` (`V0`…`V4`, member `V2_A2 = "V2+A2"`), `MetadataOutcome`
  - `db/base.py`:
    - `Base(DeclarativeBase)` with a `MetaData(naming_convention={"ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s", "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s"})`
    - mixin `IdCreatedMixin`: `id: UUID` PK with `server_default=text("gen_random_uuid()")`, `created_at: datetime` timestamptz with `server_default=func.now()`
    - `enum_check(column: str, enum: type[StrEnum], *, nullable: bool = False) -> CheckConstraint`, named `f"{column}_enum"`
  - `db/session.py`: `make_engine(url: str) -> AsyncEngine`; `make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]`.
  - `tests/conftest.py` fixtures:
    - `database_url` (session). Reads `CITEPROOF_TEST_DATABASE_URL`, default `postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof_test`. Drops and recreates that DB through the `postgres` maintenance DB, then runs `alembic upgrade head`. If the server is unreachable: skip all `db` tests, unless `CITEPROOF_REQUIRE_DB=1`, in which case fail.
    - `engine`, `session_factory` (session-scoped).
    - `clean_db` (function). `TRUNCATE` every table in `Base.metadata` with `CASCADE` before each test.
    - Tests needing the DB use `@pytest.mark.db` and the `session_factory` + `clean_db` fixtures.

- [ ] **Step 1: Compose and init SQL**
  - `db`: image `pgvector/pgvector:pg17`, user/password/db `citeproof`, port `5432:5432`, named volume, healthcheck `pg_isready -U citeproof`, mounts `docker/db/init` to `/docker-entrypoint-initdb.d`.
  - The init SQL creates databases `citeproof_eval` and `citeproof_test`.
  - `grobid`: image `lfoppiano/grobid:0.8.1`, port `8070:8070`.
  - Run `docker compose up -d db` and wait until it is healthy.

- [ ] **Step 2: Write failing tests**

```python
def test_enum_values_follow_decisions():
    assert ReferenceStatus.LOOKUP_FAILED == "LOOKUP_FAILED"
    assert UnverifiableReason.EVIDENCE_INCONCLUSIVE == "EVIDENCE_INCONCLUSIVE"
    assert [r.value for r in EvidenceRelation] == ["SUPPORTS", "PARTIALLY_SUPPORTS", "CONTRADICTS", "RELATED"]
    assert Verifier.V2_A2 == "V2+A2"

def test_enum_check_renders_in_list():
    ck = enum_check("status", JobStatus)
    assert "status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')" in str(ck.sqltext)

@pytest.mark.db
async def test_upgrade_downgrade_upgrade(database_url):
    # alembic downgrade base, then upgrade head — both succeed
    ...
    # after upgrade: SELECT extname FROM pg_extension contains 'vector' and 'pg_trgm'
```

- [ ] **Step 3: Run tests** → FAIL.
- [ ] **Step 4: Implement** enums, base, session, and Alembic env.
  - `env.py` uses `get_settings().database_url`, which the `-x url=` override can replace, and `target_metadata = Base.metadata`.
  - Migration `0001_extensions`: `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;`. Downgrade drops both.
- [ ] **Step 5: Run** `uv run pytest tests/core/test_enums.py tests/db/test_migrations.py -v && make check` → PASS.
- [ ] **Step 6: Commit** `feat(db): add enums, alembic setup and test database fixtures (T-104)`

---

### Task 4: Domain tables from 08 (T-104)

**Files:**
- Create: `src/citeproof/db/models/__init__.py` (imports every model module), `db/models/users.py`, `db/models/documents.py`, `db/models/references.py` (`reference_entries`, `works`, `reference_checks`), `db/models/sources.py` (`source_files`, `source_chunks`), `db/models/citations.py` (`citations`, `citation_links`, `claims`), `db/models/runs.py` (`verification_runs`, `claim_verifications`, `evidence`), `db/models/feedback.py`, `migrations/versions/0002_domain_tables.py`
- Test: `tests/db/test_domain_constraints.py`, `tests/db/factories.py` (`async def make_user(session) -> User`, `async def make_document(session, user: User | None = None) -> Document`, `async def make_run(session, document: Document) -> VerificationRun`, `async def make_claim(session, document: Document) -> Claim`; Task 5 and Task 7 reuse these)

**Interfaces:**
- Consumes: `Base`, `IdCreatedMixin`, `enum_check`, enums (Task 3).
- Produces: ORM classes `User`, `Document`, `ReferenceEntry`, `Work`, `ReferenceCheck`, `SourceFile`, `SourceChunk`, `Citation`, `CitationLink`, `Claim`, `VerificationRun`, `ClaimVerification`, `Evidence`, `Feedback`. Columns are exactly those of 08 §4.2 as updated on 23/11/2026, including the 07a additions:
  - `works.title_norm`
  - `reference_checks`: `sources_failed`, `resolved_by`, `agent_run_id`, `match_score`, `needs_user_review`
  - `source_chunks.embedding_model`
  - `claim_verifications`: `schema_version`, `verifier`, `agent_run_id`, `escalated_from`
- Decisions the doc leaves open (made here):
  - `source_chunks.embedding` is `Vector(1024)` (ADR-007 is open; 1024 matches BGE-M3 and multilingual-e5-large).
  - `source_chunks.tsv` is `TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED`. The `simple` config is used because the corpus is bilingual and has no Vietnamese stemmer.
  - `verification_runs` adds `warnings JSONB NOT NULL DEFAULT '[]'` (needed by the 09 `Run` contract) and `cost_estimate NUMERIC(12,6) NOT NULL DEFAULT 0`.
  - `source_files.access_status` is `TEXT` without CHECK. Its values come from v1.0 §9.3, which is not in the repo yet (P0-08).
  - The `agent_run_id` columns are created in Task 5 together with `agent_runs`, not here.
  - FKs from document-owned tables use `ON DELETE CASCADE`: `reference_entries`, `citations`, `claims`, `verification_runs` → `documents`; children → their parents. `documents.user_id` uses `ON DELETE CASCADE`.
  - Unique constraints: `works(doi)`; `reference_checks(run_id, reference_entry_id)`; `claim_verifications(run_id, claim_id, verifier)`.
  - CHECKs (08 §4.4):
    - `claim_verifications`: `status <> 'UNVERIFIABLE' OR unverifiable_reason IS NOT NULL`
    - `reference_checks`: `status <> 'NOT_FOUND_IN_SEARCHED_SOURCES' OR cardinality(sources_searched) > 0`
    - `source_files`: `origin <> 'USER_UPLOAD' OR (owner_user_id IS NOT NULL AND reference_entry_id IS NOT NULL)`
    - plus `enum_check` on every enum column
  - Index: GIN `pg_trgm` (`gin_trgm_ops`) on `works.title_norm`; GIN on `source_chunks.tsv`. No HNSW index (07a TK-19).

- [ ] **Step 1: Write failing tests** (each inserts the minimal parent rows through a helper `make_document(session) -> Document` in `tests/db/factories.py`)

```python
@pytest.mark.db
async def test_unverifiable_requires_reason(session_factory, clean_db): ...  # insert UNVERIFIABLE, reason NULL → IntegrityError
@pytest.mark.db
async def test_not_found_requires_sources_searched(...): ...  # NOT_FOUND_IN_SEARCHED_SOURCES with [] → IntegrityError; LOOKUP_FAILED with [] → OK
@pytest.mark.db
async def test_user_upload_requires_owner_and_reference(...): ...
@pytest.mark.db
async def test_reference_check_unique_per_run_and_entry(...): ...
@pytest.mark.db
async def test_claim_verification_unique_per_run_claim_verifier(...): ...  # same claim with V2 and "V2+A2" → OK; duplicate V2 → IntegrityError
@pytest.mark.db
async def test_invalid_enum_value_rejected(...): ...  # reference_checks.status = 'FAKE' → IntegrityError
@pytest.mark.db
async def test_delete_document_cascades(...): ...  # after DELETE document: 0 rows in reference_entries, claims, verification_runs, claim_verifications, evidence
@pytest.mark.db
async def test_models_match_migrations(database_url): ...  # alembic `check` command raises nothing
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement the models.** Generate `0002` with `uv run alembic revision --autogenerate -m "domain tables"`, then hand-edit it:
  - generated `tsv` column (`sa.Computed`)
  - trgm/GIN indexes
  - `pgvector.sqlalchemy.Vector` import

  Rename the file to `0002_domain_tables.py`.
- [ ] **Step 4: Run** `uv run pytest tests/db -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(db): add domain tables and integrity constraints (T-104)`

---

### Task 5: Ops and memory tables from 07a (T-104, 07a §4.7.6)

**Files:**
- Create: `src/citeproof/db/models/llm.py` (`llm_calls`, `llm_cache`, `metadata_cache`), `db/models/jobs.py`, `db/models/agents.py` (`agent_runs`), `db/models/retrieval.py` (`retrieval_results`), `migrations/versions/0003_ops_memory_tables.py`
- Modify: `db/models/references.py`, `db/models/runs.py` (add `agent_run_id` FK columns, `ON DELETE SET NULL`)
- Test: `tests/db/test_ops_memory_constraints.py`

**Interfaces:**
- Produces: ORM `LLMCall`, `LLMCacheEntry`, `MetadataCacheEntry`, `Job`, `AgentRun`, `RetrievalResult`, with columns exactly per 08 §4.2 (updated) and 07a §4.7.6. Plus:
  - `jobs`: `UNIQUE(run_id, stage)`; index `(status, run_after)`; `attempts INT NOT NULL DEFAULT 0`; `run_after` timestamptz default `now()`.
  - `llm_cache`: PK `cache_key`; CHECK `namespace NOT LIKE 'doc:%' OR document_id IS NOT NULL`; `document_id` FK `ON DELETE CASCADE`.
  - `metadata_cache`: PK `(source, query_hash)`; no FKs.
  - `llm_calls.run_id` and `llm_calls.agent_run_id`: FK `ON DELETE SET NULL`.
  - `agent_runs.run_id`, `retrieval_results.run_id`: FK `ON DELETE CASCADE`.
  - `retrieval_results`: `UNIQUE(run_id, claim_id, source_chunk_id, query_variant)`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.db
async def test_doc_namespace_cache_requires_document_id(...): ...  # namespace "doc:x", document_id NULL → IntegrityError; "eval:D2@abc" with NULL → OK
@pytest.mark.db
async def test_delete_document_removes_doc_cache_agent_runs_retrieval(...): ...
@pytest.mark.db
async def test_llm_calls_survive_document_deletion(...): ...  # row remains, run_id IS NULL
@pytest.mark.db
async def test_job_unique_per_run_stage(...): ...
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the models and autogenerate `0003`; review the generated FKs and CHECKs.
- [ ] **Step 4: Run** `uv run pytest tests/db -v && make check` → PASS (including `test_models_match_migrations`).
- [ ] **Step 5: Commit** `feat(db): add jobs, llm cache, agent and retrieval tables (T-104)`

---

### Task 6: PostgreSQL job queue (T-105)

**Files:**
- Create: `src/citeproof/pipeline/__init__.py`, `src/citeproof/pipeline/jobs.py`
- Test: `tests/pipeline/test_jobs.py`

**Interfaces:**
- Consumes: `Job`, `JobStatus`, `StageName` (Tasks 3, 5).
- Produces:
  - `@dataclass(frozen=True) class ClaimedJob: id: UUID; run_id: UUID; stage: StageName; attempts: int; payload: dict[str, Any]`
  - `async def enqueue(session: AsyncSession, run_id: UUID, stage: StageName, payload: dict[str, Any] | None = None, run_after: datetime | None = None) -> bool`. Uses `INSERT … ON CONFLICT (run_id, stage) DO NOTHING`. Returns `True` if inserted.
  - `async def claim_next(session: AsyncSession, worker_id: str, lock_timeout: timedelta) -> ClaimedJob | None`. Selects one row where `(status='QUEUED' AND run_after <= now()) OR (status='RUNNING' AND locked_at < now() - lock_timeout)`, ordered by `run_after`, with `FOR UPDATE SKIP LOCKED`. Sets `RUNNING`, `attempts += 1`, `locked_at`, `locked_by`. The caller commits.
  - `async def mark_done(session: AsyncSession, job_id: UUID) -> None`
  - `async def mark_failed(session: AsyncSession, job_id: UUID, error: BaseException, *, retryable: bool, max_attempts: int, backoff_base_seconds: float) -> JobStatus`. Reschedules as `QUEUED` with `run_after = now() + backoff_delay(attempts, base)` when `retryable and attempts < max_attempts`; otherwise sets `FAILED`. Stores `last_error = f"{type(error).__name__}: {error}"[:2000]`.
  - `def backoff_delay(attempts: int, base_seconds: float) -> timedelta`, computed as `base * 2 ** (attempts - 1)`.

- [ ] **Step 1: Write failing tests**

```python
def test_backoff_delay_doubles():
    assert [backoff_delay(n, 5.0).total_seconds() for n in (1, 2, 3)] == [5.0, 10.0, 20.0]

@pytest.mark.db
async def test_enqueue_is_idempotent(...): ...  # second enqueue of (run, S1) returns False; 1 row
@pytest.mark.db
async def test_claim_respects_run_after(...): ...  # job with run_after in the future is not claimed
@pytest.mark.db
async def test_two_sessions_claim_different_jobs(...): ...  # two open transactions, two jobs → distinct ids
@pytest.mark.db
async def test_stale_running_job_is_reclaimed(...): ...  # RUNNING with locked_at 1h ago, lock_timeout 15min → reclaimed, attempts incremented
@pytest.mark.db
async def test_retryable_failure_reschedules_then_fails(...): ...  # max_attempts=2: first → QUEUED with run_after≈now+5s; second → FAILED
@pytest.mark.db
async def test_non_retryable_failure_fails_immediately(...): ...
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `pipeline/jobs.py` with SQLAlchemy Core statements (`select(...).with_for_update(skip_locked=True)`, `postgresql.insert(...).on_conflict_do_nothing`).
- [ ] **Step 4: Run** `uv run pytest tests/pipeline/test_jobs.py -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(pipeline): add postgres job queue with skip-locked claim and backoff (T-105)`

---

### Task 7: Stage DAG, runner, worker, resume, per-unit processing (T-105)

**Files:**
- Create: `src/citeproof/pipeline/stages.py`, `pipeline/runs.py`, `pipeline/units.py`, `pipeline/worker.py`, `pipeline/__main__.py`
- Test: `tests/pipeline/test_runner.py`, `tests/pipeline/test_units.py`, `tests/pipeline/helpers.py` (`async def drain(session_factory, registry, settings, worker_id="w1") -> int`, which returns the number of jobs processed; `CountingStage`; `FailingStage`)

**Interfaces:**
- Consumes: Task 6 queue functions; `VerificationRun`, `RunStatus`; `Settings`; `RetryableError`, `InputError`.
- Produces:
  - `STAGE_DEPENDENCIES: Mapping[StageName, frozenset[StageName]]`, exactly: S1←∅, S2←{S1}, S3←{S2}, S4←{S2}, S5←{S3,S4}, S6←{S5}, S7←{S6}, S8←{S7}, S9←{S8}.
  - `@dataclass(frozen=True) class StageResult: warnings: tuple[str, ...] = ()`
  - `class PipelineStage(Protocol): name: StageName; async def run(self, run_id: UUID) -> StageResult`
  - `class NoopStage` (implements it; `__init__(self, name: StageName)`)
  - `class StageRegistry`: `__init__(self, stages: Iterable[PipelineStage])` raises `ValueError` unless all nine stages appear exactly once; `get(self, name: StageName) -> PipelineStage`; `@classmethod noop(cls) -> StageRegistry`.
  - `pipeline/runs.py`:
    - `async def create_run(session, *, document_id: UUID, user_id: UUID, config: dict[str, Any]) -> UUID`. Inserts a run with status `QUEUED` and enqueues `S1`.
    - `async def complete_stage(session, job: ClaimedJob, result: StageResult) -> None`. Locks the run row with `SELECT … FOR UPDATE`, then: `mark_done`; appends warnings; sets run `RUNNING`; enqueues each successor whose dependencies are all `DONE`. When `S9` is done, sets the run to `COMPLETED`, or `COMPLETED_WITH_WARNINGS` if warnings exist, and sets `finished_at`.
    - `async def fail_stage(session, job: ClaimedJob, error: BaseException, settings: Settings) -> None`. Calls `mark_failed` with `retryable=isinstance(error, RetryableError)`. If the job is now `FAILED`, sets the run to `FAILED` with `error = {"stage": …, "type": …, "message": str(error)[:500]}`.
    - `async def resume_run(session, run_id: UUID) -> int`. Raises `InputError` unless the run is `FAILED`. Resets that run's `FAILED` jobs to `QUEUED` with `attempts=0` and `run_after=now()`, sets the run to `RUNNING`, clears `error`, and returns the number of jobs requeued.
  - `pipeline/worker.py`:
    - `async def work_once(session_factory: async_sessionmaker[AsyncSession], registry: StageRegistry, worker_id: str, settings: Settings) -> bool`. Claims a job in one transaction, runs the stage outside it under `log_context(run_id=…, stage=…, job_id=…)`, then calls `complete_stage` or `fail_stage` in a new transaction. Returns `False` when nothing was claimable.
    - `async def run_worker(session_factory, registry, worker_id: str, settings: Settings, stop: asyncio.Event) -> None`. Loops `work_once` and sleeps `worker_poll_interval_seconds` when idle.
  - `pipeline/units.py`:
    - `@dataclass(frozen=True) class UnitReport: processed: int; skipped: int; failed: tuple[tuple[str, BaseException], ...]`
    - `async def process_units(units: Sequence[U], *, key: Callable[[U], str], done_keys: Collection[str], handle: Callable[[U], Awaitable[None]], concurrency: int) -> UnitReport`. Skips units whose key is in `done_keys`, bounds concurrency with a semaphore, and collects per-unit exceptions instead of raising.
  - `python -m citeproof.pipeline` runs `run_worker` with `StageRegistry.noop()` and a worker id `f"{hostname}:{pid}"`.

- [ ] **Step 1: Write failing tests** (a helper `drain(session_factory, registry, settings)` calls `work_once` until it returns `False`; a `CountingStage` records calls; a `FailingStage(fail_times: int, error: BaseException)` raises the first `fail_times` calls)

```python
@pytest.mark.db
async def test_empty_run_completes_end_to_end(...):
    # create_run → drain → run.status == COMPLETED; 9 jobs DONE; S5 job created after both S3 and S4 DONE

@pytest.mark.db
async def test_run_fails_at_s5_and_resumes_from_s5(...):
    # S5 = FailingStage(1, InputError("boom")); drain → run FAILED, error["stage"] == "S5"
    # resume_run == 1; drain → COMPLETED; CountingStage calls: S1..S4 == 1 each, S5 attempted twice

@pytest.mark.db
async def test_retryable_error_is_rescheduled_not_failed(...):
    # S2 = FailingStage(1, RetryableError("429")); after first drain: job QUEUED with run_after in future, run not FAILED
    # set run_after = now() in DB; drain → COMPLETED

@pytest.mark.db
async def test_warnings_make_completed_with_warnings(...): ...

@pytest.mark.db
async def test_concurrent_workers_enqueue_s5_once(...):
    # S3, S4 stages await a shared asyncio.Barrier(2) so they finish together; two work_once loops via asyncio.gather
    # → exactly one S5 job row; run COMPLETED

async def test_process_units_skips_done_and_collects_failures():
    # units a,b,c,d; done_keys {"a"}; handle raises on "c" → processed 2, skipped 1, failed [("c", exc)]

async def test_process_units_respects_concurrency():
    # 10 units, concurrency 3; max simultaneous handles observed == 3
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `stages.py`, `runs.py`, `units.py`, `worker.py`, `__main__.py` per the signatures.
- [ ] **Step 4: Run** `uv run pytest tests/pipeline -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(pipeline): add stage DAG runner, worker loop, resume and unit processing (T-105)`

---

### Task 8: Health API, Dockerfile, full Compose stack (T-102)

**Files:**
- Create: `src/citeproof/api/__init__.py`, `src/citeproof/api/app.py`, `docker/Dockerfile`, `.dockerignore`
- Modify: `docker-compose.yml` (add `migrate`, `app`, `worker`)
- Test: `tests/api/test_health.py`

**Interfaces:**
- Consumes: `Settings`, `make_engine`.
- Produces:
  - `create_app(settings: Settings | None = None) -> FastAPI`.
  - `GET /api/v1/health`: runs `SELECT 1`. On success: `200 {"status": "ok", "database": "ok"}`. On any DB error: `503 {"status": "degraded", "database": "unavailable"}`.

- [ ] **Step 1: Write failing tests** with `httpx.AsyncClient(transport=ASGITransport(app))`

```python
@pytest.mark.db
async def test_health_ok(database_url): ...  # 200, body == {"status": "ok", "database": "ok"}
async def test_health_degraded_when_db_unreachable(): ...  # database_url port 1 → 503, database == "unavailable"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `api/app.py`.
  - Dockerfile: base `python:3.12-slim`; install uv via `COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /bin/uv`; `uv sync --frozen --no-dev`; non-root user.
  - Compose:
    - `migrate` runs `alembic upgrade head` once and depends on `db` being healthy.
    - `app` runs `uvicorn --factory citeproof.api.app:create_app --host 0.0.0.0 --port 8000` and depends on `migrate` completing successfully.
    - `worker` runs `python -m citeproof.pipeline` with the same dependency.
    - Every service gets `CITEPROOF_DATABASE_URL=postgresql+psycopg://citeproof:citeproof@db:5432/citeproof`.
- [ ] **Step 4: Run** `uv run pytest tests/api -v && make check` → PASS.
  Then `docker compose up -d --build && curl -s localhost:8000/api/v1/health`. Expected: `{"status":"ok","database":"ok"}`.
- [ ] **Step 5: Commit** `feat(api): add health endpoint, Dockerfile and compose services (T-102)`

---

### Task 9: Prompt templates, data blocks, short IDs, ContextBuilder (T-108)

**Files:**
- Create: `src/citeproof/llm/__init__.py`, `src/citeproof/llm/context.py`, `src/citeproof/llm/prompts.py`, `tests/fixtures/prompts/demo/v1.md`
- Test: `tests/llm/test_context.py`, `tests/llm/test_prompts.py`

**Interfaces:**
- Produces (`llm/context.py`):
  - `class UnknownShortIdError(LookupError)`, with attributes `short_id: str` and `prefix: str`.
  - `class IdMap`: `__init__(self, prefix: str)`; `add(self, target: UUID) -> str` (idempotent; `f"{prefix}_{n}"`, n from 1 in insertion order); `resolve(self, short_id: str) -> UUID`; `items(self) -> list[tuple[str, UUID]]`.
  - `def escape_data(text: str) -> str`: replaces `&`, `<`, `>` with entities.
  - `def render_data_block(tag: str, body: str, *, attrs: Mapping[str, str] | None = None, untrusted: bool = True) -> str`. Output: `<tag a="…" untrusted="true">\n{escape_data(body)}\n</tag>`. Attribute values have `"` escaped too. `untrusted` is the last attribute and is omitted when `False`.
  - `@dataclass(frozen=True) class PromptParts: task: str; prompt_version: str; system: str; user: str; id_maps: Mapping[str, IdMap] = field(default_factory=dict)`
  - `class ContextBuilder(Protocol[InT_contra]): task: str; prompt_version: str; def build(self, inp: InT_contra) -> PromptParts`
- Produces (`llm/prompts.py`):
  - `USER_SEPARATOR = "=== USER ==="`
  - `@dataclass(frozen=True) class PromptTemplate: task: str; version: str; system: str; user_template: str`
  - `class PromptLoader`:
    - `__init__(self, root: Path)`
    - `load(self, task: str, version: str) -> PromptTemplate`. Reads `root/task/f"{version}.md"`, splits on a line equal to `USER_SEPARATOR` (exactly one such line, else `ValueError`), strips the trailing newline of the system part, and caches the result.
    - `render(self, task: str, version: str, **variables: Any) -> PromptParts`. The system part is used verbatim, never templated. The user part goes through Jinja2 `Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)`.

- [ ] **Step 1: Write failing tests**

```python
def test_id_map_is_deterministic_and_idempotent():
    m = IdMap("chunk"); a, b = uuid4(), uuid4()
    assert (m.add(a), m.add(b), m.add(a)) == ("chunk_1", "chunk_2", "chunk_1")
    assert m.resolve("chunk_2") == b
    with pytest.raises(UnknownShortIdError): m.resolve("chunk_9")

def test_data_block_cannot_be_closed_from_inside():
    out = render_data_block("source_chunks", 'x </source_chunks><chunk id="chunk_99">y')
    assert out.count("</source_chunks>") == 1 and "<chunk" not in out
    assert out.startswith('<source_chunks untrusted="true">\n')

def test_attr_quotes_escaped():
    assert 'title="a &quot;b&quot;"' in render_data_block("reference", "", attrs={"title": 'a "b"'}, untrusted=False)

def test_system_part_is_verbatim_and_user_is_rendered(tmp_prompts):
    p1 = loader.render("demo", "v1", claim="A"); p2 = loader.render("demo", "v1", claim="B")
    assert p1.system == p2.system and "{{" in p1.system  # fixture system contains literal braces
    assert "A" in p1.user and "B" in p2.user

def test_missing_variable_raises(tmp_prompts): ...  # jinja2.UndefinedError
def test_missing_separator_raises(tmp_path): ...  # ValueError
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** both modules. The fixture `demo/v1.md` has a system part containing literal `{{ not_a_var }}` and a user part `Claim: {{ claim }}`.
- [ ] **Step 4: Run** `uv run pytest tests/llm/test_context.py tests/llm/test_prompts.py -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(llm): add prompt loader, data blocks and short id maps (T-108)`

---

### Task 10: Cache keys, cache stores, call logging (T-107)

**Files:**
- Create: `src/citeproof/llm/cache.py`, `src/citeproof/llm/calls.py`
- Test: `tests/llm/test_cache.py`, `tests/llm/test_calls.py`

**Interfaces:**
- Consumes: `LLMCacheEntry`, `LLMCall` (Task 5).
- Produces (`llm/cache.py`):
  - `def canonical_json(value: Any) -> str`. Uses `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`. Every `str` (keys and values, recursively) is `unicodedata.normalize("NFC", …)`. `UUID` and `datetime` are converted with `str()` / `isoformat()`; `BaseModel` with `model_dump(mode="json")`.
  - `def worker_cache_key(*, provider: str, model: str, task: str, prompt_version: str, schema_version: str, inputs: Mapping[str, Any], attempt: int) -> str`: sha256 hex of `canonical_json` of all arguments as one dict.
  - `def agent_step_cache_key(*, agent: str, agent_version: str, tool_catalog_version: str, model: str, messages: Sequence[Mapping[str, Any]]) -> str`
  - `def doc_namespace(document_id: UUID) -> str` → `f"doc:{document_id}"`; `def eval_namespace(dataset: str, dataset_hash: str) -> str` → `f"eval:{dataset}@{dataset_hash}"`.
  - `@dataclass(frozen=True) class CacheEntryMeta: namespace: str; document_id: UUID | None; task: str; model: str; prompt_version: str; schema_version: str`
  - `class CacheStore(Protocol)`: `async def get(self, key: str) -> dict[str, Any] | None`; `async def put(self, key: str, meta: CacheEntryMeta, response: dict[str, Any]) -> None`
  - `InMemoryCacheStore`. `PgCacheStore(session_factory)`: `get` increments `hit_count`; `put` is `INSERT … ON CONFLICT DO NOTHING`. Both stores raise `ValueError` when `meta.namespace` starts with `doc:` and `document_id is None`.
- Produces (`llm/calls.py`):
  - `@dataclass(frozen=True) class LLMCallRecord: run_id: UUID | None; stage: str | None; task: str; provider: str; model: str; prompt_version: str; input_tokens: int; output_tokens: int; latency_ms: int; cost_estimate: Decimal; cache_hit: bool; cache_key: str; cache_namespace: str; agent_run_id: UUID | None = None; step_index: int | None = None`
  - `class CallLogger(Protocol): async def log(self, record: LLMCallRecord) -> None`; `InMemoryCallLogger` (attribute `records: list[LLMCallRecord]`); `PgCallLogger(session_factory)`
  - `def estimate_cost(model: str, input_tokens: int, output_tokens: int, prices: Mapping[str, tuple[float, float]]) -> Decimal`: `(in*p_in + out*p_out) / 1_000_000`; an unknown model gives `Decimal(0)`.

- [ ] **Step 1: Write failing tests**

```python
def test_nfc_and_nfd_inputs_share_cache_key():
    nfc = unicodedata.normalize("NFC", "Nguyễn"); nfd = unicodedata.normalize("NFD", "Nguyễn")
    assert nfc != nfd
    assert worker_cache_key(**BASE, inputs={"a": nfc}, attempt=1) == worker_cache_key(**BASE, inputs={"a": nfd}, attempt=1)

def test_key_ignores_dict_order_but_tracks_versions_and_attempt(): ...  # {"a":1,"b":2} vs {"b":2,"a":1} equal; prompt_version/schema_version/attempt change → different

def test_namespaces():
    d = UUID(int=1)
    assert doc_namespace(d) == f"doc:{d}" and eval_namespace("D2", "ab12") == "eval:D2@ab12"

async def test_doc_namespace_without_document_rejected():
    with pytest.raises(ValueError): await InMemoryCacheStore().put("k", CacheEntryMeta("doc:x", None, "t", "m", "v1", "s1"), {})

@pytest.mark.db
async def test_pg_cache_roundtrip_counts_hits(...): ...  # put; get twice → same dict; hit_count == 2

def test_estimate_cost():
    assert estimate_cost("m", 1_000_000, 500_000, {"m": (2.0, 10.0)}) == Decimal("7")
    assert estimate_cost("unknown", 10, 10, {}) == Decimal(0)
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** `uv run pytest tests/llm -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(llm): add canonical cache keys, cache stores and call logging (T-107)`

---

### Task 11: LLMClient with cache modes, schema retry, scripted provider (T-107)

**Files:**
- Create: `src/citeproof/llm/types.py`, `src/citeproof/llm/providers/__init__.py`, `llm/providers/base.py`, `llm/providers/scripted.py`, `src/citeproof/llm/client.py`
- Test: `tests/llm/test_client.py`

**Interfaces:**
- Consumes: `PromptParts` (Task 9); `worker_cache_key`, `CacheStore`, `CacheEntryMeta`, `CallLogger`, `LLMCallRecord`, `estimate_cost` (Task 10); `LLMOutputError`.
- Produces:
  - `llm/types.py`:
    - `class OutputModel(BaseModel)`: `model_config = ConfigDict(extra="forbid")`; `SCHEMA_VERSION: ClassVar[str]`. This is the base class for every LLM output schema.
    - `@dataclass(frozen=True) class ProviderRequest: model: str; system: str; messages: tuple[Mapping[str, str], ...]; output_schema: Mapping[str, Any]; max_tokens: int`. It deliberately has no sampling or tool-choice fields.
    - `@dataclass(frozen=True) class ProviderResponse: text: str; input_tokens: int; output_tokens: int; stop_reason: str`
    - `class CacheMode(StrEnum): READ_WRITE = "read_write"; REPLAY_ONLY = "replay_only"; FRESH = "fresh"`
    - `@dataclass(frozen=True) class CallContext: namespace: str; document_id: UUID | None; run_id: UUID | None; stage: str | None`
    - `@dataclass(frozen=True) class LLMResult(Generic[T]): value: T; cache_hit: bool; cache_key: str; input_tokens: int; output_tokens: int`
    - `class CacheMissError(CiteProofError)` with attribute `cache_key: str`.
  - `llm/providers/base.py`: `class LLMProvider(Protocol): name: str; async def complete(self, request: ProviderRequest) -> ProviderResponse`
  - `llm/providers/scripted.py`: `class ScriptedProvider`. Has `name = "scripted"`, `__init__(self, responses: Sequence[ProviderResponse | BaseException])`, and attribute `requests: list[ProviderRequest]`. Raises `AssertionError` when the script is exhausted.
  - `llm/client.py`: `class LLMClient`, with `__init__(self, provider: LLMProvider, cache: CacheStore, logger: CallLogger, *, mode: CacheMode = CacheMode.READ_WRITE, prices: Mapping[str, tuple[float, float]] | None = None)` and
    `async def complete_structured(self, *, parts: PromptParts, schema: type[T], model: str, ctx: CallContext, max_tokens: int = 4096, max_attempts: int = 2) -> LLMResult[T]`.
  - Behaviour of `complete_structured`, per attempt `n = 1..max_attempts`:
    1. Compute `inputs = {"system": parts.system, "messages": messages}`. `messages` starts as `[{"role": "user", "content": parts.user}]`. Then `key = worker_cache_key(provider=provider.name, model=model, task=parts.task, prompt_version=parts.prompt_version, schema_version=schema.SCHEMA_VERSION, inputs=inputs, attempt=n)`.
    2. Cache lookup.
       - Unless `FRESH`: a cache hit is validated into `schema`, logged with `cache_hit=True`, 0 tokens and cost 0, and returned.
       - `REPLAY_ONLY` with a miss raises `CacheMissError`.
    3. Call the provider with `output_schema = schema.model_json_schema()`. Log the attempt (tokens, latency, cost).
    4. Validate the output. A `stop_reason` in `{"max_tokens", "refusal"}` or a `ValidationError` from `schema.model_validate_json(text)` makes the attempt invalid. In that case, append `{"role": "assistant", "content": text}` and `{"role": "user", "content": feedback}` to `messages` and try again. `feedback` begins with `"SCHEMA_INVALID:"` and lists each error as `loc: msg` (no input values). After the last attempt, raise `LLMOutputError(f"{parts.task}: output invalid after {max_attempts} attempts")`.
    5. On success, if `READ_WRITE`, `cache.put(key, CacheEntryMeta(ctx.namespace, ctx.document_id, parts.task, model, parts.prompt_version, schema.SCHEMA_VERSION), {"value": value.model_dump(mode="json")})`. Return the result.

- [ ] **Step 1: Write failing tests** (schema `Echo(OutputModel)` with `SCHEMA_VERSION = "echo/1"` and field `answer: str`; in-memory stores)

```python
async def test_miss_calls_provider_then_hit_skips_it(): ...  # 2 calls to complete_structured, provider.requests length 1, 2nd result.cache_hit
async def test_replay_only_raises_on_miss(): ...  # CacheMissError
async def test_fresh_mode_neither_reads_nor_writes(): ...  # pre-seeded cache ignored; store unchanged
async def test_schema_error_retries_with_feedback(): ...  # script: '{"bad":1}', '{"answer":"ok"}' → ok; requests[1].messages[-1]["content"].startswith("SCHEMA_INVALID:")
async def test_feedback_does_not_echo_input_values(): ...  # invalid output containing "bí mật" → feedback text does not contain "bí mật"
async def test_two_invalid_outputs_raise_llm_output_error(): ...
async def test_max_tokens_stop_reason_is_invalid(): ...  # valid JSON but stop_reason "max_tokens" → retried
async def test_every_provider_attempt_is_logged_with_cost(): ...  # 2 attempts → 2 records; cost from prices
async def test_doc_namespace_cache_entry_carries_document_id(): ...
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `types.py`, `providers/`, `client.py`.
- [ ] **Step 4: Run** `uv run pytest tests/llm -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(llm): add LLMClient with cache modes, schema retry and scripted provider (T-107)`

---

### Task 12: SSRF-safe HTTP client (T-109)

**Files:**
- Create: `src/citeproof/integrations/__init__.py`, `src/citeproof/integrations/http_safe.py`
- Test: `tests/integrations/test_http_safe.py`

**Interfaces:**
- Produces:
  - `class UnsafeUrlError(InputError)`, with attribute `code: Literal["SCHEME_NOT_ALLOWED", "HOST_NOT_ALLOWED", "DNS_FAILED", "TOO_MANY_REDIRECTS"]`.
  - `class FetchError(RetryableError)` for timeouts and connection errors.
  - `@dataclass(frozen=True) class SafeResponse: status_code: int; final_url: str; headers: Mapping[str, str]; body: bytes; truncated: bool`
  - `Resolver = Callable[[str, int], Awaitable[list[str]]]`. The default uses `asyncio.get_running_loop().getaddrinfo` and returns IP strings.
  - `class SafeHttpClient`, with `__init__(self, *, resolver: Resolver | None = None, transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 10.0, max_bytes: int = 5_000_000, max_redirects: int = 5)` and `async def get(self, url: str) -> SafeResponse`.
  - Rules (07a §4.9):
    - Only `http` and `https` are allowed.
    - Parse the host with `ipaddress` first, which catches literal and decimal forms (`2130706433`). Otherwise resolve it.
    - Every resolved IP must satisfy `ip.is_global`, after unwrapping `ipv4_mapped` for IPv6. If any IP is not global, raise `HOST_NOT_ALLOWED`.
    - Connect to the first validated IP. The request URL uses that IP, with header `Host: <hostname>` and `extensions={"sni_hostname": hostname}` for https.
    - Use `follow_redirects=False` and handle redirects manually; every `Location` goes through the same validation, up to `max_redirects`.
    - Use a fresh `httpx.AsyncClient` per call. Send no cookies, no auth and no `Referer` header.
    - Stream the body and stop at `max_bytes` with `truncated=True`.

- [ ] **Step 1: Write failing tests** (fake resolver dict; `httpx.MockTransport` that records requests)

```python
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://x"])
async def test_scheme_blocked(url): ...  # UnsafeUrlError.code == "SCHEME_NOT_ALLOWED"

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://localhost:5432/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://10.0.0.5/", "http://100.64.0.1/",
])
async def test_private_targets_blocked(url): ...  # HOST_NOT_ALLOWED (resolver maps localhost → 127.0.0.1)

async def test_bypass_forms_are_blocked():
    # (a) public host whose resolver returns ["93.184.216.34", "10.0.0.1"] → HOST_NOT_ALLOWED
    # (b) public host 302 → http://127.0.0.1/admin → HOST_NOT_ALLOWED, and no request ever sent to 127.0.0.1

async def test_connects_to_validated_ip_with_host_header():
    # example.org → 93.184.216.34; recorded request url host == "93.184.216.34", headers["host"] == "example.org"

async def test_body_capped(): ...  # 10 KB body, max_bytes=1000 → len(body)==1000, truncated
async def test_too_many_redirects(): ...  # TOO_MANY_REDIRECTS
async def test_no_cookie_or_auth_headers_sent(): ...
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `http_safe.py`.
- [ ] **Step 4: Run** `uv run pytest tests/integrations -v && make check` → PASS.
- [ ] **Step 5: Commit** `feat(integrations): add SSRF-safe HTTP client (T-109)`

---

### Task 13: CI, pre-commit, task tracking (T-106)

**Files:**
- Create: `.github/workflows/ci.yml`, `.pre-commit-config.yaml`
- Modify: `docs/task-tracking.md` (T-101…T-109 → `COMPLETED`), `docs/project-status.md` (history row)

**Interfaces:**
- Produces: a CI run on every push and PR.

- [ ] **Step 1: Write `ci.yml`**
  - One job on `ubuntu-latest`.
  - Service container `pgvector/pgvector:pg17` (env `POSTGRES_USER/PASSWORD/DB=citeproof`, port 5432, health-cmd `pg_isready`).
  - Steps:
    - `actions/checkout@v4`
    - `astral-sh/setup-uv@v6` with `version: "0.13.3"`
    - `uv sync --frozen`
    - `make check`
    - `uv run pytest` with env `CITEPROOF_TEST_DATABASE_URL=postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof_test` and `CITEPROOF_REQUIRE_DB=1`
  - A second job runs `gitleaks/gitleaks-action@v2` with `fetch-depth: 0`.
- [ ] **Step 2: Write `.pre-commit-config.yaml`** with hooks for ruff (check `--fix`, format) and gitleaks. Run `uv run pre-commit run --all-files` → all hooks pass.
- [ ] **Step 3: Full local verification**
  Run `docker compose up -d db && make check && CITEPROOF_REQUIRE_DB=1 uv run pytest -q`. Expected: all tests pass, 0 skipped.
- [ ] **Step 4: Update docs**
  - `docs/task-tracking.md`: T-101…T-109 → `COMPLETED`. T-202 stays `NOT_STARTED`.
  - `docs/project-status.md`: add the row `| <date> | M1 nền tảng hoàn tất trên nhánh feat/m1-foundation (T-101…T-109) |`.
- [ ] **Step 5: Commit** `ci: add GitHub Actions workflow and pre-commit hooks (T-106)`
