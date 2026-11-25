# Core pipeline: Task 14–26 completion ledger (session 2)

Branch `feat/core-pipeline`, commits 03e3ac2..621fb8f. Final check: `CITEPROOF_REQUIRE_DB=1 uv run pytest -q` → 1649 passed, 2 skipped; `make check` clean. Session-1 rulings and deferred minors: see [2026-11-24-core-pipeline-handoff.md](2026-11-24-core-pipeline-handoff.md) appendices A/B.

Below: verbatim controller ledger for session 2 (task completions, rulings, parked and deferred minors).

Task 14: dispatched implementer (sonnet, agent a3775bc07309423b9), BASE 03e3ac2
Env: session 2 had no uv/.venv/db; controller installed uv (user approved), uv sync, docker compose up -d db. Baseline 03e3ac2: 1224 passed, 2 skipped, make check clean. Task 14 implementer resumed to finish.
Task 14: implementer DONE_WITH_CONCERNS, commit 32bc3af (1264 passed); no true RED (tests written without tooling). Reviewer dispatched (opus, af179de7a18ad3f55) on review-03e3ac2..32bc3af.diff
Task 14: review 1 — spec ✅; Important: (1) resume test passes regardless (cache hit + ON CONFLICT); (2) entry_works drops child NULL work_id row before precedence, parent wins; (3) plan-mandated decimals never match lexically.
Task 14: Ruling: plan-mandated decimal tokenisation (Important #3) → build_tsquery also emits each `\d+[.,]\d+` as one quoted token normalised to `.` (alongside the \w+ tokens) — spec intent is that numbers in claims find evidence; simple parser indexes 95.7 as one token — cost: slightly longer queries.
Task 14: Ruling: ⚠️ W3 cost vs QĐ-32 limits → W3 calls are recorded in llm_calls and count toward run cost; enforcement of max_cost_usd stays in S8 per spec §7.8 — cost: a run with many non-en claims may spend on W3 before S8 checks the cap.
Task 14: minor (deferred): numbers_preserved substring match ("12" in "2012") — compare token sets
Task 14: minor (deferred): test_lexical ordering accepts two orders; bad_output case doesn't assert W3 attempted; fenced_test_session misnamed; STOPWORDS duplicates
Task 14: minor (deferred): zero-hit pairs write no rows → reprocessed on resume and S8 can't tell "retrieved nothing" from "not retrieved" (matters for EVIDENCE_INCONCLUSIVE precheck — check in Task 16)
Task 14: minor (deferred): retrieve() holds a lazily-connecting session across W3 — invariant only by comment
Task 14: fix round 1/5 (3 addressed, 0 open; commits 32bc3af..9338a6e)
Task 14: minor (deferred): test_search_finds_decimal has no-op replace("accuracy",""); decimal handling also emits integer/fraction parts (noise)
Task 14: complete (commits 03e3ac2..9338a6e, review clean)
Task 15: dispatched implementer (sonnet), BASE 9338a6e
Task 15: implementer DONE, commit 70093b7 (1301 passed); reviewer dispatched
Task 15: review 1 — spec ✅; Important: locate_quote IndexError on text starting with ≥2 whitespace chars.
Task 15: Ruling: Minor #4 (no mixed NFC/NFD quote test) re-graded into the fix round — Review Focus #4 explicitly names Task 15 for mixed normalisation — cost: none.
Task 15: Ruling: Minor #1 (LLM explanation not screened for forbidden words) → carry to Task 16: S8/apply_rules path must replace an explanation containing a forbidden term with deterministic text (global constraint on stored explanations) — cost: small T16 scope.
Task 15: minor (deferred): quote may end on base char before combining mark (non-Vietnamese); substring match without word boundary ("95.7%" in "195.7%")
Task 15: minor (deferred): downgrade keeps LLM wording that may contradict new status (plan-mandated downgrade_note); whitespace+combining mark dropped; _NOTES keyed by bare strings duplicated in rules.py
Task 15: fix round 1/5 (2 addressed, 0 open; commits 70093b7..607a8e5)
Task 15: complete (commits 9338a6e..607a8e5, review clean)
Task 16: dispatched implementer (opus), BASE 607a8e5
Task 16: implementer DONE, commit 1f4be9f (1351 passed); concerns: cost cap overshoot with concurrency>1, COST_LIMIT warning on retry, cap applied to zero-hit V2 pair (brief says 'before each LLM pair'), unresolved rows excluded from report (T18)
Task 16: review 1 — spec ✅; Important: (1) mid-stream httpx transport errors not mapped to RetryableError (regression in shared provider); (2) control chars in chunk.section → ValueError escapes pair isolation, fails run. ⚠️ parent-run reference_status fallback untested.
Task 16: Ruling: ⚠️ parent-run reference_status fallback is a real gap (ruling-mandated behaviour with no test) → add test in fix round — cost: none.
Task 16: Ruling: re-grade cost gate applied to zero-hit V2 pairs (no LLM call) to Important — brief says the cap is checked "before each LLM pair"; deterministic outcomes must not become PROCESSING_ERROR — cost: none.
Task 16: Ruling: re-grade forbidden-word guard lacking NFC normalisation to Important — global constraint on stored explanations — cost: none.
Task 16: minor (deferred): in-flight calls can overshoot cap with concurrency>1; LLMClient schema-retry not gated; COST_LIMIT_REACHED lost if tripping attempt fails
Task 16: minor (deferred): forbidden-word replacement drops downgrade note; hand-built <source_chunks untrusted="true"> wrapper vs render_data_block convention; reference_statuses duplicates entry_works precedence loop; LLM_NOT_CONFIGURED imported from references.stage_extract
Task 16: fix round 1/5 (5 addressed, 0 open; commits 1f4be9f..3757da4)
Task 16: complete (commits 607a8e5..3757da4, review clean)
Task 17: dispatched implementer (sonnet), BASE 3757da4
Task 17: implementer DONE, commit aa45dff (1382 passed); concerns: S7 skips V3 so no X2 for subclaims; cost cap mid-subclaims → whole pair PROCESSING_ERROR; added VerificationContext.lease. Reviewer dispatched (opus).
Task 17: review 1 — spec mostly ✅; Important: (1) W4 fallback truncates claim to 300 chars (brief says full claim_text), long single-subclaim responses fail validation → truncated claim judged; (2) V3 drops X2 cross-lingual retrieval; fixture hides it (English subclaims for vi claim).
Task 17: Ruling: V3 + X2 → when cross_lingual == "X2", deps.llm set and pair.language != "en", translate each sub-claim with W3 (translate_claim, same number check) and add query variant `subclaim:<id>:translated` alongside `subclaim:<id>`; W3 calls go through the llm_budget gate; test fixture W4 returns Vietnamese sub-claims for a Vietnamese claim — QĐ-24 (X2 exists for vi claims vs en sources, the main use case) — cost: one extra W3 call per sub-claim.
Task 17: minor (deferred): loop keeps retrieving after cost cap trips mid-subclaims; SubClaim lacks extra=forbid; aggregation forbidden-word test vacuous (no sub-claim forbidden-word test); report_head.html hard-codes variant list; ".;" joined explanation; W4 prompt_version not recorded on row; retry after W4 fallback can mix subclaim:s1 rows
Task 17: fix round 1/5 (2 addressed, 0 open; commits aa45dff..d37bbd4)
Task 17: minor (deferred): V3 W3 error policy differs from S7 (non-retryable CiteProofError fails pair instead of dropping variant; no last-attempt drop); W3 error paths in V3 untested
Task 17: complete (commits 3757da4..d37bbd4, review clean)
Task 18: dispatched implementer (opus), BASE d37bbd4
Task 18: implementer DONE, commit 00c20f8 (1401 passed); sample render changed per QĐ-31/FR-21; e2e Devlin NOT_FOUND (S2 not queried by DOI)
Task 18: review 1 — Approved. Ruling: sample rendering change accepted (spec §7.9/QĐ-31/FR-21 bind over brief's "unchanged") — cost: none.
Task 18: minor (deferred): overview "missing" todo wording now also counts REFERENCE_NOT_FOUND (double todo); missing reason chosen by func.min alphabetical; invalid stored config → report 500; e2e evidence not tied to SUPPORTED row; log-leak list lacks a looked-up title; foreign-user test partial; ClaimSheet.verifier str vs Verifier
Task 18: complete (commits d37bbd4..00c20f8, review clean)
Task 19: dispatched implementer (sonnet), BASE 00c20f8
Task 19: implementer DONE, commit 615af3a (1423 passed; code before tests, RED via stash); reviewer dispatched (opus)
Task 19: review 1 — spec ✅; Important: (1) partial_ratio false MATCH when PDF text shorter than title; (2) discard cascades into evidence of finished runs.
Task 19: Ruling: title_match → UNKNOWN when extracted text (metadata title + first two pages) is empty or shorter than the folded reference title (absorbs Minor #1) — FR-33's warning must not be silenced by a short/scanned PDF — cost: scanned uploads show "chưa kiểm tra được" instead of a guess.
Task 19: Ruling: discard_source refuses (ConflictError code SOURCE_IN_USE, Vietnamese message via labels.py + DESIGN.md) when any evidence row references the source's chunks — finished reports must not change after the fact — cost: user cannot remove an upload already used as evidence.
Task 19: minor (deferred): second upload for same reference keeps hidden older rows/files; orphan file if commit fails after put; inline copy (missing lede, "Hãy chọn một tệp PDF.", read_upload empty-file msg says PDF hoặc DOCX); FILE_TOO_LARGE msg duplicated; sample USER_UPLOAD discardable via direct POST; test gaps (status not asserted, no discard CSRF test, function-level imports)
Task 19: fix round 1/5 (2 addressed, 0 open; commits 615af3a..27eb4b3)
Task 19: minor (deferred): services/sources.py imports web.labels (layering); web test imports private _add_evidence from services tests
Task 19: complete (commits 00c20f8..27eb4b3, review clean)
Task 20: dispatched implementer (opus), BASE 27eb4b3
Task 20: implementer DONE, commit d472d1a (1450 passed); deviations: merge copies all missing parent reference_checks, RUN_NOT_FINISHED, removed per-ref 'Kiểm tra lại cả bài' button, shared lock_for_new_run; concerns: child inherits demo fail_at; progress counts skipped stages
Task 20: review 1 — spec ✅ (5 deviations accepted: Ruling — merge copies every parent reference_check the child lacks (QĐ-30 complete child report); RUN_NOT_FINISHED guard; shared lock_for_new_run; per-ref button removal OK if DESIGN.md updated — cost: none). Important: retry buttons shown on unfinished runs (always fail) + stale DESIGN.md:74.
Task 20: Ruling: child config sets demo=None (absorbs Minor #3, one line) — local demo fail_at must not leak into children — cost: none.
Task 20: minor (deferred): status read before lock (spurious RUN_NOT_FINISHED window); merge test gaps (lookup_retry with claims, S9 re-run, NULL-ref copy idempotency); is_new compares tx-start now(); function-level import in test_reverify_pages; progress page shows skipped as DONE
Task 20: fix round 1/5 (2 addressed, 0 open; commits d472d1a..66b0e88)
Task 20: complete (commits 27eb4b3..66b0e88, review clean)
Task 21: dispatched implementer (opus), BASE 66b0e88
Task 21: implementer DONE_WITH_CONCERNS, commit 4139e8b (1490 passed); T-506 left IN_PROGRESS (real datasets T-E02); harness queues S9 itself for D5 runs
Task 21: review 1 — spec ✅ (F10/F11/F17 verified pre-existing; D5 S9 enqueue accepted as job seeding, not TK-15 violation; T-506 IN_PROGRESS correct). Important: (1) product run limits silently truncate eval datasets, MISSING scored, no warnings in report; (2) bootstrap label set differs from point estimate; (3) Recall@k/MRR skip zero-retrieval claims, n mismatched; (4) same-day same-config results overwritten.
Task 21: Ruling: eval runs set limits.max_claims = number of imported claims (no truncation by claim count) and keep max_cost_usd from the benchmark config (each benchmark/configs/*.yaml states it explicitly) as the spend guard; EvalReport/RefEvalReport gain `warnings` (run warnings) and `missing_count`; report.md shows them — a truncated eval must be visible, cost guard stays — cost: none.
Task 21: Ruling: result dir never overwritten — if `<date>-<stem>` exists append `-2`, `-3`, … ; split, when given, is included in the name — B.7 workflow needs several runs per day — cost: none.
Task 21: minor (deferred): _finalize_if_idle holds DAG knowledge (comment/product rule); _drive has no deadline; D5 matching by raw text; eval shares product storage_dir; cross-file retrieval ties; unscored error/UNVERIFIABLE rates
Task 21: fix round 1/5 (4 addressed, 0 open; commits 4139e8b..ec17d4a)
Task 21: minor (deferred): V2 run with all-zero hits reports no retrieval metrics (None) instead of 0
Task 21: complete (commits 66b0e88..ec17d4a, review clean)
Task 22: dispatched implementer (sonnet), BASE ec17d4a
Task 22: implementer DONE, commit 7c3a6d4 (1503 passed); reviewer dispatched (sonnet)
Task 22: review 1 — Approved; Important (plan-mandated): progress polling every 2s consumes the per-IP "all" bucket (4 tabs or NAT → 429, UI stalls).
Task 22: Ruling: GET requests to `/runs/{id}/progress` are exempt from the limiter (authenticated, cheap, driven by htmx polling every 2 s) — spec intent is abuse protection, not throttling the app's own polling — cost: a scripted client could poll progress unthrottled (auth required).
Task 22: minor (deferred): 429 lacks security headers (limiter outside SecurityHeadersMiddleware); _prune O(n) per request; no untrusted-XFF test; rate settings lack ge=1; "unknown" shared bucket; POST /documents/text in "all" bucket
Task 22: fix round 1/5 (1 addressed, 0 open; commits 7c3a6d4..826b372)
Task 22: minor (deferred): progress regex uses match+$ (accepts trailing \n) — fullmatch stricter
Task 22: complete (commits ec17d4a..826b372, review clean)
Task 23: dispatched implementer (sonnet), BASE 826b372
Task 23: implementer DONE_WITH_CONCERNS, commit 0b5d63a (1512 passed); T-606 IN_PROGRESS (needs real host + restore drill); scripts syntax-checked only; compose config rc=0 with example env
Task 23: review 1 — Approved; Important: restore.sh doesn't wait for db health before pg_restore.
Task 23: Ruling: re-grade backup umask (dumps with user documents created 644) to Important — privacy of user content (docs/10) — fix with umask 077 — cost: none.
Task 23: minor (deferred): missing citeproof_eval aborts backup before storage tar; backup || true silent; smoke --password in argv; secrets to migrate; CHANGE_ME_ not rejected / URL-encoding docs; citeproof_test created in prod; deploy test gaps (JAVA_TOOL_OPTIONS, both DB dumps, restore eval arg, smoke CSRF); static IP reservation; ufw bypass note
Task 23: fix round 1/5 (2 addressed, 0 open; commits 0b5d63a..bf4d431); controller re-ran tests/deploy: 10 passed (report lacked pasted output)
Task 23: minor (deferred): docs/14 "Thử khôi phục" example lacks sudo; deploy tests are string-presence checks
Task 23: complete (commits 826b372..bf4d431, review clean)
Task 24: dispatched implementer (opus), BASE bf4d431
Task 24: implementer DONE, commit 5bc493e (1552 passed); concerns: reminder placement vs brief, cache key omits effort/max_tokens, tools must wrap untrusted data themselves
Task 24: review 1 — spec ✅; Important: RetryableError swallowed into ERROR (breaks job-retry rule).
Task 24: Ruling: "one call left" reminder before the final submit-only call accepted (spec §9 submit not counted; 07a budgets 6 tool turns + 1 submit) — cost: none.
Task 24: Ruling: Tool protocol docstring must state tools wrap untrusted text with render_data_block(untrusted=True) — Tasks 25/26 build on it — cost: none.
Task 24: minor (deferred): gather doesn't cancel sibling tools; LAST_CALL_TEXT may repeat after failed final submit; cache-race path logs stored entry's tokens; wall-clock timeout drops the turn from steps; step cache key omits effort/max_tokens (plan-mandated; note in ADR); cache/pricing logic duplicated with LLMClient; truncated tool_use on max_tokens consumes a step
Task 24: fix round 1/5 (2 addressed, 0 open; commits 5bc493e..f043f87)
Task 24: complete (commits bf4d431..f043f87, review clean)
Task 25: dispatched implementer (opus), BASE f043f87
Task 25: implementer DONE, commit dca98b9 (1603 passed); maybe_resolve gained gateway param; implementer chose verify thresholds; T-408 marked COMPLETED though D5 acceptance unmeasurable
Task 25: review 1 — spec mostly ✅ (gateway param accepted; implementer fix texts accepted; case-1 thresholds accepted). Important: (1) case-2 venue check subset-based (token_set_ratio) + no title floor → false matches; (2) DOI substring match in raw text; (3) PARSE_FAILED DOI match never flags review; (4) close-call MATCH clears review flag on LLM choice; (5) no boundary tests.
Task 25: Ruling: A1 case 2 uses a symmetric strict venue comparison (token_sort_ratio ≥ 90 after normalisation, not subset) and requires a title floor (title_similarity ≥ 50) — near-zero false matches is A1's acceptance criterion — cost: some genuine translated/abbreviated-venue rescues are rejected (stay NOT_FOUND/flagged). If venue_matches is shared with S3, don't change S3 behaviour; add an A1-specific helper.
Task 25: Ruling: close-call prior → A1 MATCH keeps needs_user_review=True unless the reference's own DOI equals the chosen candidate's DOI — code must decide review — cost: close calls stay flagged even when the agent is right.
Task 25: Ruling: T-408 set to IN_PROGRESS with note "D5 false-match measurement pending T-506/T-E02; T-407 decision pending" — consistent with T-506/T-606 — cost: none.
Task 25: minor (deferred): PARSE_FAILED bag-of-words title test over whole entry; agent_run_id dropped on isolated non-retryable escalation error; truncated flag not set when limit cuts groups; agent input lacks top-3 candidates/queries (CascadeResult doesn't keep them)
Task 25: fix round 1/5 (5 addressed by implementer; commits dca98b9..d7b4bd3) — side effect: title floor 50 rejects fully translated Vietnamese titles, which is exactly case 2's purpose (brief test_a1_translated_title_review).
Task 25: Ruling (revises round-1 title floor): drop the title-similarity floor for case 2; instead require strict symmetric venue match + exact year + first-author family match + second-author family match when both reference and candidate list ≥2 authors; case 2 outcome is always METADATA_MISMATCH(title) + needs_user_review — translated titles are the case-2 use; flagged outcome bounds the harm — cost: a wrong work with same venue/year/first two authors could be linked, but always flagged for review.
Task 25: fix round 2/5 (all addressed, 0 open; commits dca98b9..2e20ef0)
Task 25: minor (deferred): venues_equal accepts single-letter series siblings (Physical Review B vs D) — always flagged METADATA_MISMATCH; venue_in_raw one-directional subset; DOI regex greedy over trailing ",word" (safe false negative)
Task 25: complete (commits f043f87..2e20ef0, review clean)
Task 26: dispatched implementer (opus), BASE 2e20ef0
Task 26: implementer DONE, commit db57d64 (1636 passed); T-704 IN_PROGRESS; concerns: no X2 for A2, V4 ignores enabled flag, escalated_from only when escalated, &amp; quotes
Task 26: review 1 — spec ✅; Important: A2 path doesn't unescape quotes (&amp;/&lt;/&gt;) before verdict_check/apply_rules → correct quotes rejected.
Task 26: Ruling: no X2 translation in A2 input accepted (spec §9 and brief silent; prompt tells agent to search in source language) — cost: possibly lower A2 recall on vi claims vs en sources.
Task 26: Ruling: V2+A2 escalated_from set only when A2 ran (brief: "else a copy of V2's outcome") — cost: none.
Task 26: Ruling: re-grade Minor #1 (missing retry-branch tests; last-attempt RetryableError drops agent_run_id) into the fix round — controller ruling singled out the retry rule — cost: none.
Task 26: minor (deferred): V4 ignores enabled flag (comment); READ_LIMIT_EXCEEDED never emitted; single-chunk truncation measured on raw not escaped text; S7 retrieval runs for V4; redundant full_text check; assert-based narrowing; parametrized escalation test lacks escalated_from assert
Task 26: fix round 1/5 (2 addressed, 0 open; commits db57d64..0508570)
Task 26: minor (deferred): _latest_agent_run may pick an earlier attempt's ERROR row if last attempt failed before its row was written; function-level import in test_quotes
Task 26: complete (commits 2e20ef0..0508570, review clean)
Final review: starting
Final review: With fixes. Important: backup.sh aborts before storage tar when citeproof_eval missing; `|| true` silent.
Final review: Ruling: one fix wave covers Important #1 + runbook "stop workers before alembic upgrade" (Task 1 deferred) + Minor #1 (discard_source refuses while document has an active run) + Minor #2 (child run S9 merges parent run warnings) — cheap, user-visible consistency — cost: small extra scope.
Final review: parked — Minor #3 (parent keeps offering reverify after child used uploads) — Ruling: deferred, cost ~0 via LLM cache — cost if wrong: duplicate child runs.
Final review: parked — Minor #4 (refusal fallback keyed on model prefix, not task) — Ruling: deferred follow-up; holds under default config — cost if wrong: W1–W4 on Sonnet get fallback.
Final review: parked — Minor #5 (cache tokens priced at full input rate) — Ruling: conservative overestimate, deferred (Task 2 ruling) — cost: cost cap trips slightly early.
Final review: parked — Minor #6 (migration 0010 unique index without dedupe) — Ruling: deferred, no pre-pilot data — cost if wrong: migration fails on a DB with duplicate OA rows.
Final review fix wave: commit 621fb8f; re-review: all addressed, no new Critical/Important. Controller verified: 1649 passed, 2 skipped; make check clean.
Final review: minor (deferred): backup retention skipped when a dump aborts; discard guard doesn't lock document row (narrow race); RUN_IN_PROGRESS copy inline, not in labels.py/DESIGN.md
