# Completion Audit — CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Ngày audit | 27/11/2026 |
| Commit | `56cb260` (`main`, trùng `origin/main`) |
| Phạm vi | `src/`, `tests/`, `migrations/`, `docker/`, `scripts/`, `benchmark/`, `.github/`, `docs/` (gồm `docs/superpowers/` plans, specs, handoff) |
| Kết luận ngắn | **Phần mã MVP đã chạy được đầu-cuối và qua toàn bộ kiểm tra tự động. Dự án chưa hoàn thiện 100%**: còn việc vận hành (pilot), dữ liệu và thực nghiệm (M0, T-E, T-7xx), vài lỗ hổng cấu hình/tài liệu, và khoảng 90 việc nhỏ đã hoãn. |

---

## 1. Kết quả kiểm tra thực tế (chạy ngày 27/11/2026)

| Lệnh | Kết quả |
|---|---|
| `uv run ruff check .` | `All checks passed!` (exit 0) |
| `uv run ruff format --check .` | `361 files already formatted` (exit 0) |
| `uv run mypy` (strict) | `Success: no issues found in 194 source files` (exit 0) |
| `CITEPROOF_REQUIRE_DB=1 uv run pytest -q` (DB `citeproof-db-1` bật bằng `docker compose up -d db`) | **1649 passed, 2 skipped** trong 124,5 s (exit 0) |
| 2 test bị skip | `tests/llm/test_anthropic_live.py` (cần `CITEPROOF_LIVE_LLM=1` + API key) và `tests/integrations/test_grobid.py` (cần `CITEPROOF_LIVE_GROBID=1`). **Chưa từng chạy với LLM thật trên máy này.** |
| `docker compose config -q` | Hợp lệ |
| `docker build -f docker/Dockerfile .` | Build thành công (image tạm đã xóa) |
| `docker compose -f docker-compose.prod.yml config` | Báo thiếu `.env.production` — đúng thiết kế (file tạo khi triển khai) |
| Quét `TODO/FIXME/XXX/HACK` | 0 kết quả trong `src`, `tests`, `scripts`, `migrations`, `docker`, `benchmark` |
| Quét `NotImplementedError`, `pass`, `...` | Không có hàm chưa cài đặt. Các chỗ còn lại là lớp exception rỗng (`web/deps.py`, `web/security.py`), `Protocol` (`llm/cache.py`, `llm/providers/base.py`, `references/cascade.py`), `except ValueError: pass` có chủ đích (`integrations/http_safe.py`), và `parsing/docx.py` chỉ *bắt* `NotImplementedError` |
| Coverage | **Không đo được**: dev deps không có `pytest-cov` |

---

## 2. Module/tính năng đã hoàn thành và chạy ổn

Căn cứ: test xanh ở mục 1, `docs/task-tracking.md` (COMPLETED), sổ handoff `docs/superpowers/handoff/2026-11-25-core-pipeline-completion.md` (Task 14–26 đều "review clean").

### 2.1 Pipeline S1–S9 (`src/citeproof/pipeline/registry.py`)

Cả 9 stage đều có cài đặt thật, không còn `NoopStage` nào trong registry:

| Stage | Module | Ghi chú |
|---|---|---|
| S1 Parse | `modules/documents/stage.py` | GROBID + dự phòng `pypdfium2`, DOCX, văn bản; ngôn ngữ, `LEGACY_ENCODING`, `SCANNED_PAGES` (T-201, T-204–T-206) |
| S2 Tách tài liệu tham khảo | `modules/references/stage_extract.py` | GROBID `processCitationList`, rule, W1 (T-207) |
| S3 Kiểm tra tài liệu tham khảo | `modules/references/stage_check.py` | Crossref/OpenAlex/Semantic Scholar, URL web, A1 leo thang (T-301, T-303, T-304) |
| S4 Trích dẫn và claim | `modules/citations/stage.py` | Marker, liên kết, tách claim W2 (T-401, T-402) |
| S5 Lấy nguồn | `modules/sources/stage_fetch.py` | Unpaywall, arXiv, ACL, OpenAlex OA (T-403) |
| S6 Lập chỉ mục | `modules/sources/stage_index.py` | Chia đoạn, tsvector (T-405) |
| S7 Truy xuất | `modules/retrieval/stage.py` | Lexical + X2 dịch W3 (T-501) |
| S8 Kiểm chứng | `modules/verification/stage.py` | V0–V4, V2+A2, quy tắc trạng thái, kiểm tra quote, giới hạn chi phí (T-502–T-504, T-704 phần mã) |
| S9 Báo cáo | `modules/reports/stage.py` | Gộp cảnh báo run cha (T-601) |

### 2.2 Nền tảng
- Job runner Postgres có lease + heartbeat (T-105, T-209), cấu hình/logging (T-103), 10 migration Alembic (T-104), CI GitHub Actions có Postgres + gitleaks (T-106).
- Lớp LLM: `LLMClient`, cache, streaming `messages.stream` → `get_final_message()`, refusal fallback, giá/chi phí (T-107, T-108); `AgentRunner` (T-406).
- `http_safe` chống SSRF (T-109); cache, rate limit, circuit breaker cho metadata (T-304).

### 2.3 Web UI (server-rendered, ADR-005)
Đã nối đầy đủ route ↔ service ↔ template, gồm: đăng nhập/đăng ký bằng mã mời/đăng xuất, đồng ý, tải lên PDF/DOCX/văn bản, tiến độ (htmx), tiếp tục từ bước lỗi, kiểm chứng lại một phần và tra cứu lại (T-505), báo cáo 4 phần, xem bằng chứng, tải lên nguồn thiếu và gỡ nguồn (T-404, T-602), phản hồi (T-603), xuất Markdown `report.md`, xóa tài liệu/tài khoản (T-605), bài mẫu và chế độ thử (T-307), giới hạn yêu cầu/phút 429 (T-608), `/api/v1/health`.

### 2.4 Đánh giá và triển khai (phần mã)
- Harness `python -m citeproof.evaluation run|run-refs` (`read_write`/`replay`/`fresh`), Macro-F1 + bootstrap, Recall@k/MRR, chi phí; `make eval`, `make eval-replay`; 5 cấu hình `benchmark/configs/`.
- `docker-compose.prod.yml` (Caddy HTTPS, backup hằng ngày), `.env.production.example`, `scripts/backup.sh`, `restore.sh`, `smoke.py`.
- CLI: `create-user`, `create-invite`, `seed-sample`.

---

## 3. Phần còn thiếu, làm dở hoặc tiềm ẩn lỗi

Mức ưu tiên: **P0** chặn pilot hoặc gây sai lệch khi dùng; **P1** nên làm trước pilot; **P2** dọn dẹp/sau pilot.

### 3.1 Lỗ hổng mã/cấu hình do audit này phát hiện (chưa có trong sổ)

| # | Ưu tiên | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|---|
| A-1 | **P0** | `docker compose up` ở local **không bao giờ nhận được `CITEPROOF_ANTHROPIC_API_KEY`**: service `app`/`worker` chỉ có `DATABASE_URL`, `STORAGE_DIR` (và `GROBID_URL` cho worker), không có `env_file`; `.dockerignore` loại `.env`, nên `env_file=".env"` của `Settings` cũng không thấy gì trong image. Mọi run chạy qua compose sẽ hạ cấp `LLM_NOT_CONFIGURED` dù người dùng đã đặt key trong `.env`. | `docker-compose.yml` dòng 31–33, 66–68; `.dockerignore`; `core/settings.py:15` | Thêm `env_file: [{path: .env, required: false}]` cho `migrate`/`app`/`worker`, giữ `DATABASE_URL`/`GROBID_URL` ghi đè trong `environment`; thêm test trong `tests/deploy/`. Cũng nên truyền `CITEPROOF_ENV` rõ ràng. |
| A-2 | P1 | README sai so với thực tế: "Các bước xử lý thật (đọc bài, tra cứu, đối chiếu) chưa có: bài bạn tải lên sẽ chạy hết 9 bước nhưng báo cáo trống." S1–S9 đã có. README cũng chưa nói cách bật LLM, GROBID, `make eval`. | `README.md` mục "Thực nghiệm giao diện" | Viết lại mục này; thêm hướng dẫn API key (sau khi sửa A-1). |
| A-3 | P1 | `docs/project-status.md` dừng ở 20/11/2026, "Phase 0 IN_REVIEW", Phase 6 "NOT_STARTED" — mâu thuẫn với ~1650 test và pipeline đầy đủ. | `docs/project-status.md` | Cập nhật phase, milestone M1–M6 và hành động tiếp theo. |
| A-4 | P2 | `docs/task-tracking.md` ghi "Đã có (nhánh `feat/core-pipeline`)" ở nhiều dòng, nhưng nhánh đã gộp vào `main`. `origin/feat/core-pipeline` còn ở `03e3ac2` (cũ); `feat/core-pipeline` local = `main`. | `git rev-parse` | Sửa chú thích thành "trên `main`"; xóa hoặc đẩy nhánh `feat/core-pipeline` (cần bạn quyết). |
| A-5 | P2 | Không có công cụ đo coverage. | `pyproject.toml` dev group | Thêm `pytest-cov`, báo cáo coverage trong CI (không đặt ngưỡng cứng lúc đầu). |
| A-6 | P1 | Chưa từng chạy luồng đầu-cuối với LLM thật và GROBID thật (2 test live luôn skip; handoff ghi "Chưa có API key Claude trên máy"). Toàn bộ kết quả S8 hiện chỉ được kiểm bằng LLM giả. | Mục 1 | Sau A-1: chạy `CITEPROOF_LIVE_LLM=1`, `CITEPROOF_LIVE_GROBID=1`, rồi một bài thật qua UI; ghi chi phí thực tế mỗi bài (đầu vào cho T-008/T-703). |

### 3.2 Task đang làm dở (IN_PROGRESS trong `task-tracking.md`)

| Task | Phần còn thiếu | Loại việc |
|---|---|---|
| T-202 Object storage | Backend S3-compatible (mới có local). Đang hoãn theo quyết định: pilot dùng volume + backup. | Mã (hoãn) |
| T-203 Parse PDF | Đo ngưỡng G-5 trên mẫu bài thật (cần T-003, T-005) | Dữ liệu |
| T-208 Bộ golden parsing | Mẫu bài thật đã kiểm tay (mới có 10 mẫu tổng hợp) | Dữ liệu |
| T-302 So khớp | Đo trên mẫu thật | Dữ liệu |
| T-408 Agent A1 | Đo tỷ lệ khớp sai trên D5; chờ quyết định T-407 | Dữ liệu + quyết định |
| T-506 Harness đánh giá | Nhập D1–D5 thật (T-E02…T-E07) | Dữ liệu |
| T-604 Xuất báo cáo | **Xuất HTML in được** (Markdown đã có) | **Mã** |
| T-606 Triển khai pilot | Chọn máy chủ + tên miền, chạy thật, diễn tập khôi phục, theo dõi uptime (checklist `14` §5) | Vận hành |
| T-704 Agent A2 | Chạy thực nghiệm so sánh | Thực nghiệm |

### 3.3 Task chưa bắt đầu (NOT_STARTED)

- **M0 nghiên cứu (T-001…T-010):** literature review, dùng thử công cụ hiện có, phân tích mẫu bài, phỏng vấn người dùng, spike SPK-01…05, gán nhãn thử, chốt go/no-go. Toàn bộ là việc ngoài mã; nhiều task IN_PROGRESS ở 3.2 phụ thuộc vào đây.
- **Dữ liệu đánh giá (T-E01…T-E07):** annotation guideline, D1–D5, lượt gán nhãn thứ hai.
- **Thực nghiệm (T-701…T-703, T-705):** chạy chính, ablation, chi phí/độ trễ, quyết định chế độ leo thang.
- **Pilot (T-306 Pilot-α, T-607 Pilot-β), T-407 quyết định A1, T-801 cải tiến theo pilot, T-802 gia cố, T-901 bản thảo bài báo, T-902 báo cáo dự án.**

### 3.4 Quyết định đang chờ bạn (đang chạy bằng mặc định, theo handoff §5)

| Câu hỏi | Mặc định | Ảnh hưởng |
|---|---|---|
| **Q-03: tên và link chính sách dữ liệu nhà cung cấp LLM trên màn hình đồng ý** | Màn hình hiện `[chưa chốt nhà cung cấp]` (`web/templates/pages/consent.html:15`) | **Chặn pilot** (đồng ý không đầy đủ thông tin) |
| Q-01: hạn mức chi phí mỗi run | 5 USD, ≤ 150 cặp | Chi phí pilot |
| Model W5 / W1–W4 | `claude-sonnet-5-5` / `claude-haiku-5-5` | Chất lượng/chi phí |
| Refusal fallback | Bật, chỉ W5 | |
| Email polite pool Crossref/OpenAlex | Không đặt | OpenAlex không key ~100 lượt tìm/ngày/IP |
| Tên miền, máy chủ pilot | Placeholder | Chặn T-606 |
| archive.org, V3 trong phạm vi, S3 storage | Tắt / Có / Hoãn | |

### 3.5 Việc nhỏ đã hoãn từ review (rủi ro tiềm ẩn)

Sổ handoff ghi **47 dòng** `minor (deferred)` phiên 1 (`2026-11-24-core-pipeline-handoff.md` Phụ lục B) và **~45 dòng** phiên 2 (`2026-11-25-core-pipeline-completion.md`). Chưa có bước phân loại. Những mục có tác động thật nhất theo đánh giá của audit:

| Ưu tiên | Mục | Nguồn |
|---|---|---|
| P1 | Migration 0010 tạo unique index không khử trùng lặp → fail trên DB có dòng OA trùng (parked #6) | Final review |
| P1 | Worker cũ để lại job `RUNNING` với `lease_expires_at` NULL sau migration không bao giờ được thu hồi — runbook đã thêm "dừng worker trước `alembic upgrade`", nhưng mã chưa `coalesce` | Task 1 |
| P1 | Phản hồi metadata mà mọi bản ghi bị bỏ qua được cache là `not_found` → schema đổi thì báo sai "không tồn tại" cả tuần; cache key không có schema version | Task 8 |
| P1 | `check_url` coi 4xx (403/429 chặn bot) là link chết | Task 9 |
| P1 | Cặp không truy xuất được gì không ghi dòng → bị xử lý lại khi resume; S8 không phân biệt "không tìm thấy" và "chưa tìm" | Task 14 |
| P1 | `numbers_preserved` so chuỗi con (`"12"` khớp trong `"2012"`); quote khớp không theo ranh giới từ (`95.7%` trong `195.7%`) | Task 14, 15 |
| P1 | Chi phí có thể vượt trần khi concurrency > 1; giá cache token tính theo giá input đầy đủ (ước cao) | Task 16, final #5 |
| P1 | 429 không có security headers (limiter nằm ngoài `SecurityHeadersMiddleware`) | Task 22 |
| P1 | Lệnh `smoke.py --password` lộ mật khẩu trong argv; `CHANGE_ME_` không bị từ chối khi khởi động prod | Task 23 |
| P2 | Bộ tách câu của chunker lệch với `citations/sentences.py` (PGS.TS., cs.) — điểm cắt đã đóng băng vào chunk | Task 13 |
| P2 | Test deploy chỉ kiểm chuỗi có mặt; nhiều `httpx.AsyncClient` không đóng trong test; `httpx2` chưa khai báo ở dev deps | Task 23, 3, 2 |
| P2 | Phân lớp: `services/sources.py` import `web.labels`; pipeline ném `NotFoundError` có chữ UI | Task 19, 3 |
| P2 | Các mục parked: run cha vẫn mời kiểm chứng lại sau khi run con đã dùng upload (#3); refusal fallback theo prefix model thay vì task (#4) | Final review |

---

## 4. Đề xuất các đợt làm nốt

Đề xuất chỉ gồm việc làm được bằng mã trong repo. Việc nghiên cứu, dữ liệu, pilot (3.3) cần bạn hoặc người dùng thật nên không đưa vào.

| Đợt | Nội dung | Ước lượng |
|---|---|---|
| **1. Sửa chặn và tài liệu sai** | A-1 (env cho compose + test), A-2 README, A-3 project-status, A-4 chú thích task-tracking | Nhỏ, 1 phiên |
| **2. Phân loại và sửa việc hoãn P1** | Phân loại ~90 dòng deferred thành sửa/chấp nhận/ghi giới hạn; sửa các mục P1 ở 3.5 theo TDD, mỗi mục một commit | Trung bình, 1–2 phiên; cần plan theo `writing-plans` |
| **3. T-604 xuất HTML in được** | Template HTML in được từ dữ liệu báo cáo hiện có, route `/runs/{id}/report.html`, test | Nhỏ–trung bình; cần brainstorming ngắn về bố cục |
| **4. Kiểm tra live** | A-6: chạy test live và một bài thật end-to-end; A-5 coverage | Cần API key của bạn; tốn phí LLM thật |
| (Không làm nếu không yêu cầu) | T-202 S3 backend | Đang hoãn theo quyết định |

Cần bạn quyết trước khi làm: Q-03 (nội dung màn hình đồng ý), có xóa nhánh `feat/core-pipeline` không, và có cấp API key để chạy đợt 4 không.
