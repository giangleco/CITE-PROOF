# Bàn giao: core pipeline (Task 1–13 xong, tiếp từ Task 14)

| Thuộc tính | Giá trị |
|---|---|
| Ngày | 24/11/2026 |
| Nhánh | `feat/core-pipeline` (chưa push), tách từ `main` tại `1e15d84` |
| Kế hoạch | `docs/superpowers/plans/2026-11-24-core-pipeline.md` (26 task) |
| Spec (ràng buộc) | `docs/superpowers/specs/2026-11-24-core-pipeline-design.md` + ADR-004, ADR-006, ADR-007 |
| Tiến độ | Task 1–13 COMPLETED, đã qua review; còn Task 14–26 |
| Kiểm tra cuối | `make check` sạch; `CITEPROOF_REQUIRE_DB=1 uv run pytest -q` → 1224 passed, 2 skipped (2 test live chỉ chạy khi bật cờ) |

## 1. Việc bạn (Claude phiên sau) cần làm

Người dùng muốn **build toàn bộ phần mềm còn lại một lượt, không hỏi từng phần nhỏ**, tối ưu sau. Tiếp tục đúng quy trình đã dùng:

1. Đọc tài liệu này, rồi đọc phần đầu kế hoạch (Global Constraints, Review Focus) và spec.
2. Gọi skill `superpowers:subagent-driven-development` với:
   `Plan: docs/superpowers/plans/2026-11-24-core-pipeline.md. Spec: docs/superpowers/specs/2026-11-24-core-pipeline-design.md. Branch: feat/core-pipeline. Resume at Task 14.`
3. Workspace của kế hoạch: `.superpowers/sdd/2026-11-24-core-pipeline/` (gitignored, chỉ có trên máy này). Sổ `progress.md` ở đó ghi mọi task, phán quyết, việc hoãn. Nếu thư mục còn, skill sẽ đọc `references/resuming.md` và tiếp tục từ Task 14. Nếu mất, dùng mục 5–7 dưới đây và `git log` để dựng lại sổ.
4. Bản quét pre-flight (49 phát hiện F01–F49 kèm phán quyết, nhiều cái áp cho Task 14–26) có bản sao đã commit: [`2026-11-24-core-pipeline-preflight.md`](2026-11-24-core-pipeline-preflight.md). F02–F49 được chấp nhận; F01 bị bác (commit giữ dòng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`); F36 sửa lại: phiên này không có context7, dùng skill `claude-api` và PyPI.
5. Mỗi task: trích brief bằng `scripts/task-brief`, giao implementer (model ghi rõ), review package bằng `scripts/review-package`, giao reviewer, vòng sửa theo `references/fix-loop.md`, ghi sổ. Sau Task 26: review toàn nhánh (model mạnh nhất), một đợt sửa, rồi `superpowers:finishing-a-development-branch`.
6. Không push, không merge khi người dùng chưa yêu cầu. Trả lời người dùng bằng tiếng Việt.

## 2. Trạng thái từng task

| Task | Nội dung | Commit (base..head) | Ghi chú |
|---|---|---|---|
| 1 | Gia hạn lease job (T-209), `StageContext`, `fenced_session` | f379cf2..659f1c0 | `fenced_session` khóa `FOR KEY SHARE` (không phải `FOR SHARE` như dòng 84 của kế hoạch) |
| 2 | Provider Anthropic sau `LLMClient` (ADR-006) | 659f1c0..84a011e | SDK `anthropic` 2.13; fallback phía server chỉ cho model `claude-sonnet*`/`claude-opus*` |
| 3 | `RunConfig`, `PipelineDeps`, `build_registry`, demo dạng lớp bọc | 84a011e..7758707 | `open_deps` đóng client LLM và HTTP |
| 4 | Bộ golden (T-208) | 7758707..8fe3976 | `tests/fixtures/golden/`; TEI ghi từ GROBID 0.8.1 thật |
| 5 | S1 cho văn bản và DOCX, nhận diện ngôn ngữ, TCVN3/VNI | 8fe3976..7f64e92 | gói `citeproof.parsing` |
| 6 | S1 cho PDF: GROBID + `pypdfium2` | 7f64e92..c0927cd | lấy danh mục từ text pdfium khi GROBID trả `listBibl` rỗng |
| 7 | S2 tách và phân tích tài liệu tham khảo, W1 | c0927cd..767f3ec | cảnh báo `REFERENCE_PARSE_DEGRADED` |
| 8 | Connector Crossref/OpenAlex/Semantic Scholar, cache, rate limit, breaker | 767f3ec..955f3f1 | thứ tự cache → breaker; `openalex_api_key` tùy chọn |
| 9 | S3 tra cứu, so khớp, trạng thái | 955f3f1..6bba66f | migration 0008 |
| 10 | S4 câu, marker, liên kết, claim | 6bba66f..395dac1 | migration 0009; marker có số trang `[1, tr. 25]` |
| 11 | W2 tinh chỉnh claim | 395dac1..80ad4b3 | |
| 12 | S5 lấy toàn văn mở và tóm tắt | 80ad4b3..3e30b4f | migration 0010; rate limit theo host OA; nội dung màn hình đồng ý mới |
| 13 | S6 chia đoạn và lập chỉ mục | 3e30b4f..e0f1e94 | `Embedder`/`NoEmbedder`; bỏ qua mục danh mục tài liệu tham khảo |

Việc còn lại (Task 14–26): S7 tìm đoạn + W3 (14); Verdict v2 và quy tắc trạng thái (15); S8 W5 + V0/V1/V2 (16); V3 (17); S9 + báo cáo trên dữ liệu thật + test đầu-cuối (18); tải nguồn ở tab "Thiếu toàn văn" (19); kiểm chứng lại một phần (20); harness đánh giá (21); rate limit mỗi phút (22); cấu hình triển khai pilot (23); `AgentRunner` (24); agent A1 (25); agent A2, V4, V2+A2 (26).

## 3. Việc chuyển tiếp cho các task sau (đã ghi trong sổ)

- **Task 14:** child run bắt đầu ở S6 chỉ chọn upload của chính nó, nên `choose_sources` phải gộp nguồn của run cha. Đọc phần hand-off trong `task-13-report.md`.
- **Task 16:** W5 (Sonnet, effort high, max 16000 token) với timeout 120 s không stream dễ quá hạn. Dùng streaming qua `get_final_message` theo skill `claude-api`, hoặc timeout ≥ 600 s.
- **Task 18:** báo cáo hiện inner-join `claim_verifications` với `reference_entries`. Từ Task 16 sẽ có dòng `reference_entry_id` NULL (REFERENCE_UNRESOLVED), nên `get_overview` và danh sách nhận định phải đếm khớp nhau (pre-flight row 37).
- **Task 19:** khóa lưu file upload phải kết thúc bằng `.pdf` hoặc `.txt`, nếu không S6 không tạo đoạn.
- **Task 21:** mọi chỗ tự dựng `PipelineDeps(...)` (harness đánh giá) phải truyền `source_limits` và `source_http`.
- **Chung:**
  - Schema đầu ra LLM tránh `oneOf`, discriminated union, tuple (giới hạn của `strict_output_schema`).
  - Mọi stage gọi mạng hoặc LLM trước, rồi mới ghi trong một `fenced_session` ngắn.
  - Việc CPU nặng chạy qua `asyncio.to_thread`.
  - Lỗi cache (`CacheMissError`/`CacheEntryInvalidError`) và `InternalError` luôn ném lại, không cô lập theo từng mục.

## 4. Môi trường

- **Docker Compose:** `db` (Postgres + pgvector), `grobid` (0.8.1, cần `JAVA_TOOL_OPTIONS=-XX:-UseContainerSupport`), `migrate`, `app` (cổng 8000), `worker`. Mọi cổng chỉ mở trên `127.0.0.1`. Container đang chạy được dựng từ `main`; muốn thử code của nhánh này thì chạy `docker compose up -d --build`.
- **Test:**
  - `make check`
  - `CITEPROOF_REQUIRE_DB=1 uv run pytest -q`: test tự tạo DB `citeproof_test`.
  - Test live chỉ chạy khi bật cờ: `CITEPROOF_LIVE_GROBID=1` (marker `grobid`), `CITEPROOF_LIVE_LLM=1` kèm `CITEPROOF_ANTHROPIC_API_KEY` (marker `live`).
- **Chưa có API key Claude** trên máy: các bước LLM hạ cấp, run có cảnh báo `LLM_NOT_CONFIGURED`.
- **OpenAlex không key** bị giới hạn khoảng 100 lượt tìm mỗi ngày mỗi IP; đặt `CITEPROOF_OPENALEX_API_KEY` khi cần.
- **Tài khoản admin dev** `admin@citeproof.local` có trong DB `citeproof`. Mật khẩu không ghi ở đây; tạo tài khoản mới bằng `uv run python -m citeproof.cli create-user --email … --name … --admin`.
- **Lưu ý `.env.example`** đang ghim `CITEPROOF_CONSENT_TEXT_UPDATED_AT`. Bản `.env` nào chép từ file cũ sẽ không bắt người dùng đồng ý lại.

## 5. Câu hỏi còn mở cho người dùng (đang dùng mặc định)

| Câu hỏi | Mặc định đang dùng |
|---|---|
| Hạn mức chi phí mỗi lần chạy (Q-01) | 5 USD, tối đa 150 cặp claim–tài liệu |
| Model cho W5 | `claude-sonnet-5-5`; W1–W4 `claude-haiku-5-5` |
| Fallback phía server khi model từ chối | Bật, chỉ cho W5 (`CITEPROOF_ANTHROPIC_REFUSAL_FALLBACK=false` để tắt) |
| Chính sách lưu dữ liệu của Anthropic, tên và link chính sách nhà cung cấp trên màn hình đồng ý (Q-03) | Chưa đặt (màn hình hiện `[chưa chốt nhà cung cấp]`) |
| Object storage S3 cho pilot | Hoãn; dùng volume local và sao lưu hằng ngày |
| Tra cứu archive.org | Tắt |
| Email liên hệ cho polite pool của Crossref/OpenAlex | Không đặt |
| Tên miền và máy chủ pilot | Placeholder |
| V3 có trong phạm vi | Có |

## 6. Phán quyết đã đưa ra (trích nguyên văn sổ)

Phụ lục A bên dưới liệt kê mọi dòng `Ruling:` trong sổ, theo thứ tự.

## 7. Việc nhỏ đã hoãn (để review toàn nhánh phân loại)

Phụ lục B liệt kê mọi dòng `minor (deferred)` trong sổ. Khi tới bước review toàn nhánh, trỏ reviewer vào danh sách này.

## Phụ lục A: các dòng `Ruling:` trong sổ (35 dòng, nguyên văn)

- Defaults for open user questions (Ruling: user opted out of questions, asked to build everything): 5 USD per-run cost limit; Sonnet for W5; refusal fallback on for W5 only; S3 deferred; new consent text + consent bump; archive.org off; no contact email; pilot domain placeholder; V3 in scope — cost: config changes later.
- Ruling: F01 rejected — commits keep the session attribution `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` as the plan's Global Constraints say; the scan misread it — cost: none.
- Ruling: F36 amended — context7 is not available in this session; Anthropic SDK usage comes from the claude-api skill and the version from PyPI via `uv add anthropic` — cost: none.
- Task 1: Ruling: plan-mandated `FOR SHARE` in fenced_session (Minor #1) → change to `FOR KEY SHARE` and compute renew_lease expiry from clock_timestamp(); document on fenced_session/StageContext that fenced transactions stay short (no network/LLM I/O inside) and blocking CPU work runs via asyncio.to_thread — spec QĐ-21's intent is fencing (exclude claim/expiry), which KEY SHARE keeps, while FOR SHARE blocks the job's own heartbeat (lease lapse while alive, deadlock with fail_expired_jobs on last attempt) — cost: one small fix round now.
- Task 2: Ruling: plan-mandated W5 timeout (120 s non-streaming, 16000 max_tokens, effort high) → carry to T16: W5/agent calls use SDK streaming with get_final_message (claude-api default for long output) or a ≥600 s timeout, decided in T16 — cost: T16 scope +small.
- Task 2: Ruling: plan-mandated cost accounting gaps (fallback usage.iterations; cache read/write pricing) and broad sonnet/opus fallback prefix → deferred to final review (estimates only; no correctness impact) — cost: run_max_cost_usd slightly off.
- Task 5: Ruling: accept the extra TCVN3 guard (signature limited to 24 chars in A1–BE, ≥5% of letters) — without it English theses with ·/µ/¬ get LEGACY_ENCODING false positives — cost: a lightly-accented TCVN3 text might be missed (warning only).
- Task 5: Ruling: accept `vi` requiring ≥1 Vietnamese-only letter and short-text `en` rule — needed for the TCVN3 golden (unknown) and short test sentences — cost: heuristic misclassification on edge inputs (language only affects explanations).
- Task 6: Ruling: when GROBID's listBibl is empty (Vietnamese APA theses, e.g. vi_mixed_apa), S1 extracts the reference block from the pypdfium2 text (heading "Tài liệu tham khảo"/"References"…) and attaches it as the document's raw references so S2 can parse it (processCitationList/rules), with a warning code if the spec defines one; GROBID body paragraphs stay primary — this is the project's main input type and S2 would otherwise find no references — cost: pypdfium2 extraction runs on every PDF whose GROBID reference list is empty.
- Task 6: Ruling: Important #2 (spec threshold "zero characters") → a PDF whose every page is a scanned page (below the scanned-page threshold) raises NO_TEXT_EXTRACTED — Review Focus #1 forbids an empty completed report and stamp/page-number-only text layers defeat the zero-char check — cost: a genuinely tiny text-only PDF (<25 chars per page) is rejected.
- Task 6: Ruling: carry to Task 7 — NFC-normalise ParsedReference fields from TEI (title/authors/venue), and use one reference splitter for S2 and the pdfium reference path (align or reuse) — cost: none.
- Task 7: Ruling: Important #2 + Minor #1 (silent permanent last-attempt fallback for W1 and GROBID in S2) → keep the fallback (completing beats failing the run) but add a run warning `REFERENCE_PARSE_DEGRADED` with Vietnamese text added to .stitch/DESIGN.md §8.6 and web/labels.py together, and keep validated W1 results from batches that finished before the failure — mirrors QĐ-22's PDF_TEXT_FALLBACK pattern — cost: one new label.
- Task 7: Ruling: re-grade Minor #3 (label-only line → empty/leading-space raw; "" posted to GROBID misaligns and discards all GROBID fields) to Important — whole-document parse quality loss — cost: none.
- Task 7: Ruling: Minor #7 (tracker T-204/T-205 "Còn" text stale) fixed in the same round as F02 compliance.
- Task 8: Ruling: add optional `openalex_api_key` (SecretStr, unset by default) sent as OpenAlex `api_key` param and never logged — OpenAlex meters keyless use (~100 searches/day/IP), the pilot would hit it — cost: one setting.
- Task 8: Ruling: lookup order is cache → breaker (serve cached results even when a source's breaker is open) instead of the brief's breaker → cache — cache hits need no call to the source — cost: none.
- Task 8: Ruling: make_test_deps defaults the metadata HTTP transport to one that refuses all requests (raises) unless a transport is passed — no test may reach the real APIs by accident — cost: none.
- Task 8: Ruling: Minor #5 (Semantic Scholar DOI case) checked live by controller — mixed and lowercase DOI both return 200 — no issue — cost: none.
- Task 8: Ruling: carry to T9 — lookups that made no HTTP call (invalid DOI, empty query) must not count in sources_searched (or the gateway returns a distinct status), so NOT_FOUND_IN_SEARCHED_SOURCES always has ≥1 real successful query — cost: none.
- Task 9: Ruling: a DOI-equality hit whose title clearly disagrees (document title present and similarity below a low threshold, e.g. token_set_ratio < 60 after fold) becomes METADATA_MISMATCH with a `title` mismatch and needs_user_review, instead of MATCHED — the user must see a wrong DOI; spec's "DOI equality accepts" assumed DOIs are right — cost: a legit DOI with a translated title may show a title mismatch (reviewable).
- Task 9: Ruling: titles shorter than 4 tokens (after fold) are accepted by title similarity only when year or first-author family also agrees — token_set_ratio scores subset titles as 100 — cost: some genuine short-title matches fall to not-found/close-call.
- Task 9: Ruling: Important #1 → year/first-author corroboration applies when EITHER folded title has < 4 tokens OR the candidate's folded token count is < 60% of the reference's (subset titles); among accepted candidates rank by fewer mismatches before score — a subset title must not win with score 1.0 — cost: some genuine abbreviated-title matches need year/author agreement.
- Task 9: Ruling: accept the implementer's close-call refinement (runner-up must be accepted with ≤ mismatches) already written into spec §7.3 — avoids needless review flags for arXiv/published duplicates — cost: an unaccepted near-duplicate within 5 points no longer flags review.
- Task 9: Ruling: re-grade Minor #1 (MATCHED with empty sources_searched; PRD §4.5.2 requires "kèm nguồn đã tra") to Important — the source of the accepted record always counts as searched — cost: none.
- Task 9: Ruling: re-grade Minor #3 (family-name re-guess makes "Le Song" → "le" and hides author mismatches) to Important — re-guess only when the record's given part has ≥ 2 tokens — cost: none.
- Task 10: Ruling: accept CHECK using `IS NOT DISTINCT FROM` (spec §14 and docs/08 updated by implementer) — the brief's `=` form evaluates to NULL for NULL reasons and a CHECK accepts NULL — cost: none.
- Task 10: Ruling: numeric markers with page locators (`[1, tr. 25]`, `[1, trang 25]`, `[1, p. 25]`, `[1, pp. 25–30]`, `[1: 25]`) are detected; links go to the reference number(s) only; marker_text keeps the full bracket — common in Vietnamese theses — cost: none.
- Task 10: Ruling: accept author–year interpretations (label numbering when ≥ half entries labelled; conflicting full names never link; capitalised word before (YYYY) → unresolved citation, never a wrong link).
- Task 10: Ruling: T-402 stays IN_PROGRESS until W2 (Task 11) lands.
- Task 10: Ruling: re-grade Minor #1 (compound titles "PGS.TS.", "GS.TSKH." split sentences, claims lose opening words) to Important — dominant form in Vietnamese theses — fix: abbreviation when every "."-segment is a known abbreviation (+ "tskh.") — cost: none.
- Task 10: Ruling: re-grade Minor #3 (comma instead of semicolon: `[1, tr. 25, 3, tr. 7]` makes the citation invisible) to Important — deterministic rule: a number immediately followed by a locator is a reference number → (1, 3) — cost: none.
- Task 10: Ruling: controller confirms both implementer rules ("numbers after a locator are pages"; ambiguity handling) — spec §7.4 should list them as implementer refinements confirmed by the controller (Minor #4) — cost: none.
- Task 12: Ruling: add per-host rate limiting for OA downloads/lookups reusing Task 8's RateLimiter — arXiv ≤ 1 request per 3 s, ACL Anthology and Unpaywall ≤ 1 request/s — arXiv asks automated clients for ~1 req/3 s; parallel S5 (4 works) would violate it and risk a block — cost: slower S5 on arXiv-heavy bibliographies.
- Task 13: Ruling: accept skipping reference-list sections when chunking (cited titles in the bibliography must not come back as evidence); the spec §7.6 needs one line recording it (add in this task's fix round if any, else carry to Task 14) — cost: none.
- Task 13: Ruling: accept per-vector 1024-length check instead of an Embedder.dim field (ADR-007 deviation, embeddings unused in v1) — cost: none.

## Phụ lục B: các dòng `minor (deferred)` trong sổ (47 dòng, nguyên văn)

- Task 1: minor (deferred): job_heartbeat_seconds/job_lock_timeout_seconds accept 0/negative (Field(gt=0))
- Task 1: minor (deferred): RUNNING rows with NULL lease_expires_at (old worker after migration) never reclaimed — document "stop workers before alembic upgrade" or coalesce
- Task 1: minor (deferred): fenced_session tests: payload {"ghi": 2} block never runs; no rollback-on-exception test
- Task 1: minor (deferred): heartbeat test timing margin (1 s lease / 0.2 s beat) on loaded CI
- Task 1: minor (deferred): re-queued jobs keep previous started_at/finished_at
- Task 1: minor (deferred): test_renew_counts_from_wall_clock_not_transaction_start asserts exactly on the 0.5 s/1.5 s boundary (DB clock vs monotonic) — add margin
- Task 2: minor (deferred): tests import transitive httpx2 — declare it in the dev group
- Task 2: minor (deferred): add httpx2/httpcore2 to _QUIET_LOGGERS (INFO line per LLM request)
- Task 2: minor (deferred): llm_effort values validated only at call time
- Task 2: minor (deferred): strict_output_schema passes oneOf/discriminator/prefixItems — W-model schemas must avoid them
- Task 2: minor (deferred): test asserts private attributes of LLMClient
- Task 3: minor (deferred): load_run_info raises web-oriented NotFoundError (Vietnamese UI text) from the pipeline layer
- Task 3: minor (deferred): make_test_deps leaves httpx.AsyncClient unclosed in most tests
- Task 4: minor (deferred): vi_mixed_apa expected lists 3 refs but recorded TEI listBibl is empty — undocumented trap outside the report
- Task 4: minor (deferred): link-label test does not check markers appear in input text
- Task 4: minor (deferred): near-tautological fixture tests; conftest loads cases at import (malformed expected.json breaks whole-suite collection)
- Task 4: minor (deferred): TEI recordings carry timestamps/MD5 (not reproducible); type-ignore in generator
- Task 5: minor (deferred): DOCX text-box content silently dropped (docstring says dedupe); table-cell captions classified by regex
- Task 5: minor (deferred): parse_text_payload lacks NO_TEXT_EXTRACTED (web form already rejects blank)
- Task 5: minor (deferred): storage.put before lease check (deterministic content, benign); model_dump_json on event loop; DOCX memory up to several × 200 MB cap
- Task 6: minor (deferred): mixed label styles / running header can collapse or drop labelled entries in the pdfium reference path; running "Bibliography" headers keep only last page
- Task 6: minor (deferred): UnsupportedProtocol/ProxyError retried as transient; fallback log lacks error_code
- Task 6: minor (deferred): encrypted-PDF test does not drain through the worker; year-only line dropped by _PAGE_NUMBER_LINE; compose waits service_started not GROBID healthy
- Task 6: minor (deferred): tests/files.py imports reportlab at module top; _ends_entry treats trailing "A." as continuation
- Task 7: minor (deferred): S2 re-runs join_unlabelled over S1 references_raw (second rule)
- Task 7: minor (deferred): wrapped "Tiếng Việt."/"English." line treated as group heading (text loss, bogus entry)
- Task 7: minor (deferred): skip path loses LLM_NOT_CONFIGURED warning on retry/reuse
- Task 7: minor (deferred): W1 author validator substring match (plan-mandated) lets short family names pass
- Task 7: minor (deferred): golden citations assertion guarded by `and stub.citation_requests`; report says pdf.py unchanged (inaccurate)
- Task 7: minor (deferred): `[n]` after a blank line always restarts (wrapped title starting "[1]" would split); unbracketed `1.` restarts after unknown headings not handled; degraded warning is document-level
- Task 8: minor (deferred): all-records-skipped responses cached as not_found silently (schema drift → a week of false not-found); S2 dict without `data` treated as zero results
- Task 8: minor (deferred): no gateway-level Review Focus #5 test; cache key lacks schema version; RateLimiter docstring overstates cross-loop safety, per-process limits
- Task 8: minor (deferred): spec settings list lacks openalex_api_key; non-retryable 4xx count toward breaker; safe_http in make_test_deps still real-network without transport
- Task 9: minor (deferred): non-isolated error does not stop remaining lookups (wasted quota); domain_flag overwritten by all-failing runs; sample deletion vs S3 overwrite race (retry recovers)
- Task 9: minor (deferred): check_url treats 4xx (403/429 bot blocks) as dead; test helper cross-imports; concurrency test sleep-based; Focus #4 gaps (mixed-normalisation title, đ vs ASCII); migration 0008 theoretical case-duplicate sample DOIs
- Task 9: minor (deferred): a partly-failed source appears in both sources_searched and sources_failed (NOT_FOUND text slightly overstates coverage); long subtitled reference with no year/authors vs short candidate rejected
- Task 10: minor (deferred): [1:2]/[10:30] ratios read as citations (consequence of ruled colon form; record as known limitation in spec §7.4 during the fix)
- Task 10: minor (deferred): lossy 0009 downgrade has no seeded test; concurrent-writer re-check path untested; author–year `Tr.` case-sensitive; `[3]–[2]` / em-dash ranges; "No." abbreviation (plan-mandated)
- Task 10: minor (deferred): all-abbreviation compound tokens ending a sentence ("p.p.") swallow the boundary; [2019, tr. 5, 3, tr. 7] → (2019 unresolved, 3)
- Task 11: minor (deferred): silent W2 degradation on last-attempt RetryableError/CiteProofError (no warning, unlike S2's REFERENCE_PARSE_DEGRADED)
- Task 11: minor (deferred): W2 exception branches untested (F12 unpinned); test_demo assertion widened to COMPLETED or COMPLETED_WITH_WARNINGS; sequential per-paragraph calls without cap; redundant sentence split
- Task 12: minor (deferred): works with no open copy re-queried every run (arXiv/ACL/Unpaywall uncached); stored abstract source never refreshed when works.abstract changes
- Task 12: minor (deferred): consent cutoff 2026-11-24T00:00Z equals ship day (plan-mandated); .env.example pins CITEPROOF_CONSENT_TEXT_UPDATED_AT (copied envs keep the old date); consent checked only in web layer (queued runs fetch OA)
- Task 12: minor (deferred): concurrent _write path untested; orphaned shared files on skipped insert, put rewrites existing content-addressed file; redirect hops not rate-limited; ArxivAccess fallback only when arxiv_id empty
- Task 12: minor (deferred): test helper cross-imports and unclosed AsyncClients; 48 MB test write; WORK_CONCURRENCY hard-coded; SafeHttpClient test-only props
- Task 13: minor (deferred): chunker sentence splitter drifted from citations/sentences.py (cs., PGS.TS., single-letter abbreviations, trailing numeric markers) — unify in citeproof.parsing later; cut points are frozen into shared chunks
- Task 13: minor (deferred): window property (≥250 tokens) not general — document actual guarantee; parsed-JSON race (put before lock); concurrent-writer test covers only sequential re-check, no unique (source_file_id, chunk_index); no done marker for unreadable sources; GROBID outage doesn't stop stage early
