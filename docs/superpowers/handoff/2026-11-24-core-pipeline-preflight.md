# Pre-flight conflict scan: core pipeline plan (26 tasks)

Authority: spec `docs/superpowers/specs/2026-11-24-core-pipeline-design.md` (binding) over plan `docs/superpowers/plans/2026-11-24-core-pipeline.md`. Real code checked: `src/citeproof/{pipeline,llm,core,db/models,services,web,integrations}`, `tests/`, `docker-compose.yml`, `.env.example`, `pyproject.toml`.
Finding IDs `F##` are defined once in section 3/4 and ruled in section 5. "ok" = consistent.

## 1. Cross-task pairs (shared file or interface)

| # | Tasks | Producer -> consumer (exact names) | Result |
|---|---|---|---|
| 1 | T1-T3 | `StageContext(run_id, lease: ClaimedJob, max_attempts)`, `PipelineStage.run(ctx)`, `NoopStage`; T3 `DemoStage(inner, session_factory, *, sleep)` calls `inner.run(ctx)` | ok on shape; existing `DemoStage(name, session_factory, sleep)` + `tests/pipeline/test_demo.py` break in T1 (F04) |
| 2 | T1-T5,T6,T7,T9,T10,T12,T13,T14,T16,T18,T25 (11 pairs) | `fenced_session(session_factory, lease: ClaimedJob)`, `ctx.lease`, `ctx.is_last_attempt`, `ctx.attempt` | ok (same contract everywhere; `is_last_attempt = attempt >= max_attempts`) |
| 3 | T1-T6 | T6 `allow_fallback = ctx.is_last_attempt or deps.grobid is None` | ok; but T6 retry test drains with `job_backoff_base_seconds=5` (F22) |
| 4 | T1-T18 | `StepView.started_at/finished_at`, `mark_done/mark_failed` set `finished_at`; `expire_stale_jobs` FAILED path does not set it | mismatch, minor (F45) |
| 5 | T1,T2-existing tests | `Settings.job_lock_timeout_seconds` 900->120; `llm_prices` {}->prices | `tests/core/test_settings.py:17,19` assert old defaults, not in Files (F03) |
| 6 | T2-T3 | `make_llm_client(settings, session_factory, *, mode)`, `LLMTask`, `Settings.llm_models/llm_effort/llm_max_tokens`, `llm_prices` -> `RunConfig.resolve`, `RunConfig.task(LLMTask) -> TaskSpec` | ok; default tables duplicated in `RunConfig.load` vs Settings (F34) |
| 7 | T2-T24 | `ProviderRequest/Response`, `LLMProvider` Protocol; T24 adds `complete_turn`, `TurnRequest/TurnResponse`; `ScriptedProvider(responses=(), *, turns=())`; `AnthropicProvider.complete_turn` | ok; T24 does not state "no server-side fallback for agents" (spec QD-36) (F32) |
| 8 | T2-T21 | `LLMClient(mode=CacheMode)`; CLI modes | `CacheMode.REPLAY_ONLY="replay_only"` (types.py) vs spec CLI `replay` (F14) |
| 9 | T2 internal/T16,T24 | served model in `LLMResult.model`, `llm_calls.model`; `estimate_cost(model,...)` (calls.py:75) returns 0 for unknown model | served fallback model not in `llm_prices` -> cost 0 (F25) |
| 10 | T3-T5,T6,T7,T8,T9,T12,T13,T14,T16,T18,T24 | `PipelineDeps` fields appended: `grobid`, `metadata`, `embedder`, `agents`; `make_test_deps` extended per task | ok; T12 needs a second `SafeHttpClient` (30 MB) but deps has one `safe_http` (F08); `metadata` default None unhandled in T9 (F44) |
| 11 | T3-T17,T26 | `services/documents.py`: `start_run` stores `RunConfig.resolve(...)`; `ADMIN_VARIANTS` +V3 (T17), +V4,V2_A2 (T26) | ok on names (`Verifier.V3/V4/V2_A2` exist in enums.py); resolved config leaves A2 disabled (F31) |
| 12 | T3-T18,T19,T20 | `labels.WARNINGS` + DESIGN.md §8.6 (T3); labels `UNRESOLVED_REF`,`UNCITED_REFERENCE` (T18); `TITLE_MATCH`, buttons (T19,T20) | ok; each task edits both files together |
| 13 | T3-T20 | `RunScope(kind, reference_entry_ids, from_stage)`, `RunInfo.in_scope(ref|None)` | ok; `in_scope(None)` False => scoped child run omits NULL-link pairs, T20 merge copies them from parent |
| 14 | T4-T5,T7,T10 | `load_cases()`, `GoldenExpected`; tests use pytest fixtures `golden_case`, `golden_docx_case` | fixtures not defined anywhere (F19) |
| 15 | T4-T5 | `GoldenExpected.warnings: list[str]` vs `parse_docx()` returns only `ParsedDocument`; warnings are dicts `{"code":...}` | mismatch (F20) |
| 16 | T4-T6 | fixtures `vi_scanned_pages.pdf`, `encrypted.pdf`, `image_only.pdf`; T6 `test_page_limit` needs 3-page PDF; image_only has no recorded TEI | underspecified (F22) |
| 17 | T4-T8 | Vaswani/Devlin/He DOIs reused by metadata fixtures | ok |
| 18 | T4,T6,T8 recorders | live GROBID docker, live APIs, system DejaVu font | execution risk (F40) |
| 19 | T5-T6 | `ParsedDocument.build`, `ParseStage` PDF branch (`ParseError("PDF parsing arrives in Task 6")` placeholder), `documents/stage.py` | ok |
| 20 | T5-T7 | `ParsedDocument.paragraphs`, `RawReference`, `Paragraph`; `core/domain.ParsedReference` | ok |
| 21 | T5-T13 | `source_language(text) -> ... "other"`; `SourceChunk.language` is free TEXT (sources.py) | ok |
| 22 | T6-T13 | `parse_pdf_document(data, grobid=, allow_fallback=)` raises `InputError(NO_TEXT_EXTRACTED)`; T13 `parse_source` "returns None when no text" | mismatch (F09) |
| 23 | T6-T19 | `pdf_page_count`, `pdf_page_texts(max_pages=)`, `pdf_metadata_title` | ok |
| 24 | T7-T8 | `normalize_doi` lives in `modules/references/normalize.py`; connectors (integrations) must lowercase DOIs (`works.doi` UNIQUE) and cannot import `modules` | dependency inversion (F05) |
| 25 | T7-T9,T10 | `normalize_doi,title_norm,fold,family_name,reference_paragraph_indices` | ok |
| 26 | T8-T9 | `MetadataGateway.by_doi/search/search_text/available`, `LookupOutcome.status in {ok,not_found,error,circuit_open}`, `make_gateway_factory` | ok; retry/sleep contract broken inside T8 (F06) |
| 27 | T8-T12 | `by_doi("openalex", doi)` -> `WorkRecord.oa: list[OaLocation]` | ok |
| 28 | T8-T21 | `MetadataCache(ok_ttl=None)` for eval DB; `make_gateway_factory(settings, http, session_factory)` has no TTL switch | unreachable (F17) |
| 29 | T8-T25 | `MetadataGateway.search_text(source, query, *, year, limit)`, `available` | ok |
| 30 | T9-T12 | `works.abstract`, `reference_checks.work_id`, `upsert_work` | ok |
| 31 | T9-T25 | `CascadeResult`, `ReferenceEscalation.maybe_resolve(entry, prior, info)`, `EscalationResult(result, agent_run_id, resolved_by)`, `ReferenceEntryView`, `match_candidate`, `MatchResult`; both edit `matcher.py`, `stage_check.py` | ok |
| 32 | T9-T18 | `ReferenceCheck.mismatches` written as `list[dict]`; model is `Mapped[dict[str, Any]]` (references.py), `_mismatches()` expects list (report.py:96) | mypy/type mismatch (F29) |
| 33 | T9-T16 | `reference_checks.status` read by `PairState.reference_status` | ok |
| 34 | T10-T16 | nullable `claim_verifications.reference_entry_id`, `unresolved_needs_reason`, unique `(run,claim,ref,verifier)` NULLS NOT DISTINCT; PG17 in docker-compose.yml | ok |
| 35 | T10-T14 | `claims`, `citation_links` (incl. NULL link) -> `load_pairs`; duplicate targets in one marker (`[1,1]`) give duplicate pairs | mismatch edge (F41) |
| 36 | T10-T11 | `ClaimDraft`, `Sentence`, `build_drafts`; both edit `citations/stage.py` | ok; W2 item field `needs_previous_sentence` never consumed (F35) |
| 37 | T10-T18 | report `_claims_query` inner-joins `ReferenceEntry` (report.py:262) until T18 outer-joins | ok (T18 owns the change) |
| 38 | T12-T13 | `source_files` OPEN_ACCESS rows, `AccessStatus`, `open_source_key` -> `relevant_source_files` | ok |
| 39 | T12-T19 | `AccessStatus.USER_PROVIDED`, `document_prefix`, storage-put order (T12 put-before-insert, T19 put after flush) | ok (different flows) |
| 40 | T13-T14 | `relevant_source_files` (T13) vs `choose_sources` (T14): two implementations of source selection | duplicated logic (F33) |
| 41 | T13-T21 | eval imports sources as `OPEN_ACCESS` `.txt`; `parse_source` handles PDF and abstract JSON only | mismatch (F10) |
| 42 | T14-T16 | `ClaimPair, SourceChoice, SourceRef, load_pairs, choose_sources, load_retrieved`; `IdMap("chunk")` short ids | ok |
| 43 | T14-T17 | `Retriever.retrieve(..., queries=)`, `write_results`, `query_variant="subclaim:s<n>"`; unique `(run,claim,chunk,query_variant)` (retrieval.py) | ok; V3 on abstract-only pairs undefined (F43); fused score for hits outside top-k undefined (F48) |
| 44 | T14-T26 | `search_chunks(..., section=)` vs `SourceSearchArgs.section_id` | mapping undefined (F49) |
| 45 | T15-T16,T17,T26 | `Verdict`, `apply_rules`, `Outcome`, `RuleContext`, `deterministic_outcome`, `ExplanationLanguage` | ok |
| 46 | T16-T17 | `make_verifier` in `verifiers/__init__`, `RagVerifier`; T17 registers V3 | ok |
| 47 | T16-T26 | `VerificationOutcome.escalates_previous`, `agent_run_id`, `write_outcomes` reusing existing row id | ok |
| 48 | T16-T18 | `run_cost(session, run_id)`; module location not stated | ok (state `verification/stage.py`) |
| 49 | T16-T21 | S8 isolates every per-pair exception as `PROCESSING_ERROR`; T21 expects `CacheMissError` to FAIL the run | contradiction (F12) |
| 50 | T16-T9 | S8 isolates `InternalError`; S3 test requires `InternalError` to fail the stage | inconsistent policy (F13) |
| 51 | T18-T19,T20 | `MissingSource.reason`(T18) then `reference_entry_id`,`uploaded`(T19); `report_missing.html`, `services/report.py` edited by all three | ok |
| 52 | T19-T20 | `uploads_for_run -> dict[UUID, UploadedSource(created_at)]`; `routes/report.py` | ok |
| 53 | T19-T22 | upload bucket = `POST` path ending `/upload` | ok |
| 54 | T18-T20 | `FinalizeStage`, `merge_parent_results` called first | ok; evidence copy not idempotent (F27) |
| 55 | T20-T21 | `PARTIAL_SKIPS`, job seeding "create_partial_run-style" for S6 start | ok |
| 56 | T3,T18,T21 | `drain(session_factory, registry, settings, worker_id)` (helpers.py:14) used as `drain(build_registry(deps))` in T18; ScriptedProvider pops sequentially (scripted.py:19) under concurrency 4 | mismatch (F26) |
| 57 | T21-T3,T11,T16 | `CallContext(namespace=eval_namespace(...), document_id=None)` (T21) vs stages using `doc_namespace(document_id)` | contradiction (F11) |
| 58 | T22-existing web | `mount_web` middleware order (`web/__init__.py:160-164`, ProxyHeaders added last = outermost) | ambiguous "after" (F28) |
| 59 | T23-T6,T22 | `.env.example` keys for every Settings field; GROBID url for prod worker; `trusted_proxies` | ok; prod `db` must mount `docker/db/init` to create `citeproof_eval`; restore handles one dump (F47) |
| 60 | T24-T25 | `AgentRunner.run(agent, version, tool_catalog_version, parts, tools, submit, budget, spec, ctx, target_type, target_id)`, `SubmitTool`, `Tool`, `load_tool_catalog`, `AgentBudget.from_config`; enums `AgentName/AgentTargetType/AgentRunStatus` exist | ok |
| 61 | T24-T26 | same + `verdict_check -> Callable[[Verdict], list[ToolError]]` fits `check: Callable[[T], Sequence[ToolError]]` | ok |
| 62 | T26-T3 | `agents.evidence_investigator.enabled` default False vs V4/V2+A2 selectable by admin | contradiction (F31) |
| 63 | T26-T16/T17 | V4 on non-full-text pairs "deterministic outcome as V2 would" | contradiction (F30) |
| 64 | T2,T14,T24 on `docs/07-technical-architecture.md`; T6,T26 on `docs/task-tracking.md`; T9,T10,T12 on `docs/08` | doc edits | ok; task-tracking omitted elsewhere (F02) |
| 65 | T6-T23 / dev compose | `docker-compose.yml` worker `CITEPROOF_GROBID_URL` (T6) vs `docker-compose.prod.yml` (T23) | ok |

## 2. Per-task self-consistency

| T | Self-consistent? | Notes |
|---|---|---|
| 1 | No | `tests/core/test_settings.py:17` (900) and `tests/pipeline/test_demo.py` break, not in Files (F03,F04); `expire_stale_jobs` skips `finished_at` (F45); migration 0007 + backfill 900 s literal vs new default 120 (acceptable). Heartbeat validator vs `pipeline_settings` (900/30): ok |
| 2 | No | `tests/core/test_settings.py:19` `llm_prices == {}` (F03); `httpx2`/`anthropic` unverifiable (F36); fallback rule "only sonnet" vs spec "Sonnet/Opus" (F37); `estimate_cost` served-model gap (F25) |
| 3 | Mostly | defaults duplicated Settings vs `RunConfig.load` (F34); `resolve` leaves A2 off (F31); migrations none; settings names match spec §15 |
| 4 | Mostly | needs live docker GROBID + font (F40); `warnings: list[str]` vs dict warnings (F20); no `golden_case` fixture (F19); no 3-page PDF (F22) |
| 5 | No | `test_docx_golden` asserts warnings parse_docx cannot return (F20); dedupe test vacuous (F21); PDF placeholder `ParseError` ok |
| 6 | No | tests underspecified/backoff (F22); error codes put in message not `code=` (F46); `GROBID_UNAVAILABLE` as RetryableError ok |
| 7 | Mostly | fixture `golden_case` (F19); `normalize_doi` location (F05); migration none |
| 8 | No | Retry-After/sleep injection absent (F06); cache test assertion flawed (F07); eval TTL (F17); `abstracts.py` placement vs spec §5 (F38) |
| 9 | Mostly | migration 0008 ok; `mismatches` typing (F29); isolation policy (F13); `metadata` None (F44) |
| 10 | Mostly | migration 0009 ok (constraint names `ck_claim_verifications_unresolved_needs_reason`, old `uq_claim_verifications_run_id`); duplicate link targets (F41) |
| 11 | Mostly | dead field (F35) |
| 12 | Mostly | migration 0010 ok; second SafeHttpClient (F08); consent sentence in template vs "labels only" (F39); `consent_text_updated_at` default exists (settings.py) ok |
| 13 | No | `parse_source` None vs InputError (F09); `selection.py` created but contract unnamed, duplicates T14 (F33) |
| 14 | No | adversarial tsquery test vacuous (F23); fused score gap (F48) |
| 15 | Yes | `test_abstract_unsupported_rule` must fix `rc.verifier` explicitly (else V2 flag ambiguity) |
| 16 | No | cost-limit test racy (F24); isolation vs T9/T21 (F12,F13); `run_cost`/`write_outcomes` module unnamed |
| 17 | Mostly | abstract-only V3 undefined (F43) |
| 18 | No | e2e provider ordering + `drain` arity (F26); `mismatches` typing (F29) |
| 19 | Mostly | too-large layer (F42) |
| 20 | Mostly | evidence duplication on resume (F27); duplicates `start_run` active-run check (reuse it) |
| 21 | No | F10,F11,F12,F14,F15,F16,F17 |
| 22 | Mostly | middleware order (F28) |
| 23 | Mostly | F47; env-example test covers dict fields (list them even if commented) |
| 24 | Mostly | no-fallback for agents not stated/tested (F32) |
| 25 | Yes | `CANDIDATE_REQUIRED_FOR_MATCH` after exhausted submit attempts: treat as NO_MATCH (covered by F31-style rule: deterministic validator) |
| 26 | No | F30, F31, F49 |

## 3. Plan statements contradicting Global Constraints / spec, and reviewer-defect mandates

- F01 Global "Commits ... `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`" contradicts the session attribution rule (`Claude Sonnet 5.5`).
- F02 Global "update `docs/task-tracking.md` in the same commit" but Files list it only for T6 and T26 though T1,4,5,7-10,12-14,16,18-25 complete rows.
- F11 Spec §11/Global namespaces: T21 requires `eval:<dataset>@<hash>` + `document_id NULL` while T11/T16 and every stage build `doc_namespace(document_id)`.
- F12 T21 replay-miss must FAIL the run; T16 per-pair isolation swallows it.
- F13 T9 (InternalError fails stage) vs T16 test (InternalError isolated per pair). Spec §6.4 isolates item errors only.
- F14 Spec §11 CLI `--mode read_write|replay|fresh`; T21 `replay_only`.
- F15 T21 `make eval` in replay mode with no committed cache fails by T21's own `test_replay_missing_cache_fails_loudly`.
- F16 Spec §11 metrics (quote-drop rate, cost, latency from `llm_calls`) have no function or test in T21.
- F17 Spec §7.3 "DB đánh giá không TTL": no way to pass `ttl=None` from eval.
- F30 Spec §9: V4 = A2 for every full-text pair; T26 "others -> deterministic outcome as V2 would" (V2 on abstract calls W5).
- F31 Spec QD-47 (agents off by default, admin enables via run config) vs T17/T26 tests "admin can choose V4/V2+A2" with `RunConfig.resolve` always disabled.
- F38 Spec §5 puts `abstracts.py` in `modules/sources`; plan puts it in `integrations/metadata` and adds `sources/selection.py` (spec §17 leaves internals to builder; update §5).
- F39 Global "UI copy only from `labels.py`" vs T12 sentence in `consent.html` (existing consent copy is already template-inline, consent.html:11).
- F32 Spec QD-36 "Agent không dùng fallback": T24 silent, untested.
- F37 Spec §8.1 fallback for Sonnet/Opus; T2 test name "only_for_sonnet".
- Reviewer defects: F21 (vacuous dedupe test, T5), F23 (vacuous tsquery test, T14), F07 (T8 assertion cannot hold for exact matches), F35 (dead schema field), F33/F34 (duplicated selection logic and default tables), F24 (racy test), F26 (order-dependent scripted provider), F20 (test asserts something the function cannot return).

## 4. False assumptions about existing code

- F03 `Settings.job_lock_timeout_seconds` default 900 and `llm_prices == {}` are asserted by `tests/core/test_settings.py:17-19`; `.env.example:6` documents 900.
- F04 `PipelineStage.run(self, run_id)` (`pipeline/stages.py:28`), `DemoStage(name, session_factory, *, sleep)` + `demo_registry` (`pipeline/demo.py:27-56`), `CountingStage.run(run_id)` (`tests/pipeline/helpers.py:35`), `tests/pipeline/test_demo.py` import `DemoStage, demo_registry`.
- F06 `RetryableError` (`core/errors.py:49`) carries no `Retry-After`; no `sleep` hook in the planned gateway.
- F08 `SafeHttpClient.__init__` default `max_bytes=5_000_000` (`integrations/http_safe.py:~160`); plan's `PipelineDeps` has one `safe_http`.
- F13 `process_units` collects every `Exception` into `UnitReport.failed` (`pipeline/units.py:36-50`), so InternalError would be swallowed unless re-raised.
- F25 `estimate_cost` returns 0 for models absent from `prices` (`llm/calls.py:75-83`); cache hits log 0 tokens (`llm/client.py` `_log(..., 0, 0, 0, cache_hit=True)`).
- F26 `drain(session_factory, registry, settings, worker_id)` (`tests/pipeline/helpers.py:14`); `ScriptedProvider.complete` pops `_responses` in call order (`llm/providers/scripted.py:19`).
- F28 `mount_web`: `add_middleware(ProxyHeadersMiddleware)` is the last call (`web/__init__.py:164`) so it is outermost; a limiter added after it would be outermost instead.
- F29 `ReferenceCheck.mismatches: Mapped[dict[str, Any]]`, `server_default '{}'` (`db/models/references.py`); report reads a list (`services/report.py:96`); `services/sample.py:375` uses `cast`.
- F42 `read_upload` raises `FILE_TOO_LARGE` at route layer (`services/documents.py:104`), not in a bytes-based service.
- F45 `expire_stale_jobs` (`pipeline/jobs.py:~140`) sets FAILED without a finish timestamp.
- Verified true (no finding): `LLMCallRecord.agent_run_id/step_index`, `MetadataCacheEntry` (PK source+query_hash, expires_at), `canonical_json`, `agent_step_cache_key`, `doc_namespace/eval_namespace/validate_namespace`, `IdMap/render_data_block/PromptLoader`, `Verifier.V3/V4/V2_A2`, `ReferenceStatus.LOOKUP_FAILED/METADATA_MISMATCH`, `ResolvedBy`, `AgentRunStatus`, `Settings.env` includes `pilot`, `consent_text_updated_at`, `parent_run_id`, `source_chunks.tsv` (`simple`), `retrieval_results` unique incl. `query_variant`, `process_units`, `SafeHttpClient.get -> SafeResponse(truncated)`, `FetchError/FetchFailedError/UnsafeUrlError`, `error_response` (429 mapped), migrations at 0006, PG17 image, test DB `citeproof_test`.

## 5. Proposed rulings (one per finding)

| ID | Ruling |
|---|---|
| F01 | Ruling: use the session attribution trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and fix the Global Constraints line - the session rule overrides plan text - cost if wrong: wrong trailer on ~26 commits. |
| F02 | Ruling: add `docs/task-tracking.md` to Files of every task whose commit names a T-id and mark a row COMPLETED only when its acceptance is met, else IN_PROGRESS - Global rule - cost if wrong: stale tracker. |
| F03 | Ruling: add `tests/core/test_settings.py` to T1 and T2 Files and update the 900/`{}` assertions to 120 and the new price table - tests would otherwise be red - cost if wrong: red suite at T1/T2. |
| F04 | Ruling: T1 also changes `DemoStage.run(ctx)`/`demo_registry` signatures minimally and updates `tests/pipeline/test_demo.py`; T3 still rewrites - keeps every task green - cost if wrong: T1 not green. |
| F05 | Ruling: define `normalize_doi` in `core/domain.py` (T5) and re-export from `references/normalize.py`; connectors lowercase through it - respects module dependency rule, `works.doi` UNIQUE - cost if wrong: duplicate works / test expecting lowercase DOI fails. |
| F06 | Ruling: add `retry_after: float | None = None` kwarg to `RetryableError` (set from the header by connectors) and `sleep=asyncio.sleep` kwarg to `MetadataGateway` - needed for T8's own tests - cost if wrong: slow/unwritable tests. |
| F07 | Ruling: assert the query payload (typo'd query title/author strings) is absent from `response`, using a `not_found` entry for the title check - exact matches legitimately contain the title - cost if wrong: false test failure. |
| F08 | Ruling: add `PipelineDeps.source_http: SafeHttpClient` (max_bytes=`max_source_bytes`, total 60 s) in T12; keep `safe_http` default for URL checks - cost if wrong: 30 MB body reads on every URL check, or 5 MB PDF cap. |
| F09 | Ruling: `parse_source` catches `InputError` (NO_TEXT_EXTRACTED, PAGE_LIMIT_EXCEEDED, PDF_ENCRYPTED) and `ParseError` and returns None - spec §7.8 maps to `SOURCE_PARSE_FAILED`, not run failure - cost if wrong: one bad source fails the whole run. |
| F10 | Ruling: `parse_source` dispatches on key suffix and supports `.txt` (blank-line paragraphs, `source="text"`); eval stores `<sha>.txt` - 6 lines - cost if wrong: harness yields no chunks. |
| F11 | Ruling: add `PipelineDeps.cache_namespace: str | None` and one helper `call_context(deps, info, stage)` used by every LLM-calling stage; eval sets `eval:<dataset>@<hash>` and `document_id=None` - cost if wrong: eval cache not isolated and replay never hits (new doc UUID each import). |
| F12 | Ruling: stages re-raise `CacheMissError`/`CacheEntryInvalidError` (and `InternalError`) out of per-item isolation - makes replay failures loud per spec - cost if wrong: silent PROCESSING_ERROR in replay. |
| F13 | Ruling: item isolation catches `CiteProofError` except `InternalError` and cache errors in S3, S4, S7, S8; T16's test raises `ExternalServiceError`, T9's keeps `InternalError` - spec §6.4 - cost if wrong: programming errors hidden. |
| F14 | Ruling: CLI accepts `replay` (spec) mapped to `CacheMode.REPLAY_ONLY` (keep `replay_only` as alias) - spec binding - cost if wrong: spec/CLI mismatch. |
| F15 | Ruling: Makefile `eval` target runs `--mode read_write` (needs key) and README says replay needs a prior read_write run - replay on empty cache must fail - cost if wrong: `make eval` red. |
| F16 | Ruling: add `quote_drop_rate`, total cost and p50/p95 latency (from `llm_calls`, `claim_verifications.downgraded_by_rule`) to `EvalReport` with one test - spec §11 lists them - cost if wrong: spec gap in T-506. |
| F17 | Ruling: `make_gateway_factory(..., ttl_enabled: bool = True)`; `run_eval`/`run_reference_eval` pass False - spec §7.3 - cost if wrong: eval results change with wall-clock. |
| F18 | Ruling: create `__init__.py` in every new test directory and rename T8's file to `test_metadata_ratelimit.py` - existing tests are packages; basenames collide - cost if wrong: pytest import-mismatch error. |
| F19 | Ruling: T4 adds fixtures `golden_case`/`golden_docx_case` (parametrised over `load_cases()`) to `tests/conftest.py` - T5,T7,T10 depend on them - cost if wrong: fixture-not-found errors. |
| F20 | Ruling: T5 `test_docx_golden` asserts language/sections only; TCVN3 via `detect_legacy_encoding`; golden warnings compared as `{w["code"]}` set from `ParseStage` - `parse_docx` returns no warnings - cost if wrong: unsatisfiable test. |
| F21 | Ruling: test dedupe by calling the warning-merge helper twice (or clear `parsed_key` between runs) - second S1 run is skipped so current test asserts nothing - cost if wrong: dead test. |
| F22 | Ruling: make `vi_scanned_pages.pdf` 3 pages for the page-limit test; mock GROBID returns 500 for `image_only.pdf`; T6 retry test uses settings with `job_backoff_base_seconds=0` - cost if wrong: flaky/failing S1 tests. |
| F23 | Ruling: use input with 2+ char tokens (`"x' | & ! ( :* <-> yy"`) and assert the result is `'yy'` and not None - cost if wrong: no injection coverage. |
| F24 | Ruling: set `llm_concurrency=1` in that test and check the cap under a lock before each call - cost if wrong: flaky test. |
| F25 | Ruling: price lookup tries served model then requested model - fallback model must still be costed - cost if wrong: `max_cost_usd` never trips under fallback. |
| F26 | Ruling: add `RoutedProvider` (responses keyed by task) to `tests/pipeline/helpers.py` for T18/T21 and call `drain(session_factory, registry, settings)` - concurrent stages break sequential scripts - cost if wrong: flaky e2e. |
| F27 | Ruling: `merge_parent_results` copies evidence only for `claim_verifications` rows newly inserted (`INSERT ... RETURNING`) - evidence has no unique key - cost if wrong: duplicate evidence on resume. |
| F28 | Ruling: call `add_middleware(RateLimitMiddleware)` before the `ProxyHeadersMiddleware` call so it runs inside it and sees the forwarded IP - cost if wrong: all users share the proxy IP bucket. |
| F29 | Ruling: change annotation to `Mapped[list[Any]]` (server default stays `'{}'`, reader tolerates it) and drop the `cast` in `sample.py` - mypy strict - cost if wrong: `make check` fails in T9. |
| F30 | Ruling: V4 on abstract-only pairs uses the V1 path (W5 on abstract); no-source pairs get precheck outcomes - matches spec §9 (A2 only on full text) - cost if wrong: V4 abstract pairs mis-handled. |
| F31 | Ruling: `RunConfig.resolve` sets `agents["evidence_investigator"].enabled = variant in {V4, V2_A2}`; A1 stays off - selecting the variant is the admin's run-config opt-in - cost if wrong: V4 yields PROCESSING_ERROR for every pair. |
| F32 | Ruling: `complete_turn` always calls `messages.create` (no fallback beta) and `test_anthropic_turn_shape` asserts it - QD-36 - cost if wrong: agent trajectories served by different models. |
| F33 | Ruling: implement selection once in `sources/selection.py` (T13) and have `choose_sources` call it - cost if wrong: drift between indexed and used sources. |
| F34 | Ruling: single defaults module (e.g. `core/llm_defaults.py`) imported by Settings and `RunConfig.load` - cost if wrong: inconsistent resumed-run models. |
| F35 | Ruling: drop `needs_previous_sentence` from `ClaimExtractionItem` - unused, schema version unreleased - cost if wrong: none. |
| F36 | Ruling: drop `httpx2`; add `anthropic` via `uv add` and confirm version/API through context7 (CLAUDE.md rule 1) before T2 - not in `pyproject.toml`/`uv.lock` - cost if wrong: unresolvable dependency. |
| F37 | Ruling: fallback applies when model id starts with `claude-sonnet` or `claude-opus`; rename test - spec §8.1 - cost if wrong: Opus unprotected. |
| F38 | Ruling: keep plan placement and amend spec §5 in T8/T13 docs edit - JATS cleaner is needed by connectors - cost if wrong: layout drift only. |
| F39 | Ruling: follow existing precedent (consent copy inline in `consent.html`) and note the exception - cost if wrong: Global rule violated once. |
| F40 | Ruling: run T4/T6/T8 recorders in an environment with docker + network; if unavailable, hand-write minimal TEI/JSON fixtures and mark them in the fixture manifest - cost if wrong: T4,T6,T7,T8,T10,T18 blocked. |
| F41 | Ruling: dedupe link targets per marker preserving order in `build_drafts` - unique `(run,claim,ref,verifier)` - cost if wrong: IntegrityError in S8 on `[1,1]`. |
| F42 | Ruling: `upload_source` re-checks `len(data) > settings.max_upload_bytes` -> `FILE_TOO_LARGE` - service test targets service - cost if wrong: untestable path. |
| F43 | Ruling: V3 on abstract-only pairs behaves as V1 (no decomposition) - cost if wrong: undefined behaviour on abstracts. |
| F44 | Ruling: `CheckReferencesStage` raises `InternalError` when `deps.metadata is None` - registry always sets it - cost if wrong: silent all-LOOKUP_FAILED. |
| F45 | Ruling: `expire_stale_jobs` also sets `finished_at = now()` - spec §6.1 - cost if wrong: missing duration on expired jobs. |
| F46 | Ruling: pass `code=` kwarg (e.g. `ParseError("...", code="TEI_REJECTED")`) with Vietnamese message-free text - uses `CiteProofError(code=)` (errors.py:9) - cost if wrong: error codes only in messages. |
| F47 | Ruling: prod `db` mounts `./docker/db/init`; `restore.sh` takes optional eval dump arg - backup dumps `citeproof_eval` - cost if wrong: backup fails under `set -e` on a missing DB. |
| F48 | Ruling: compute fused over all variant hits and write each row with its fused score - cost if wrong: NULL `score_fused` rows. |
| F49 | Ruling: `source_outline` emits `sec_N` ids (IdMap) and `SourceSession` maps id -> section name for `search_chunks(section=)` - cost if wrong: section filter unusable. |
