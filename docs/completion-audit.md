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

---

## 5. Phân loại việc hoãn

Đã duyệt mọi dòng `minor (deferred)` của handoff 2026-11-24 (Phụ lục B, 47 dòng, Task 1–13), handoff 2026-11-25 (27 dòng `minor (deferred)` của Task 14–26 và final review, cùng 4 mục `parked` #3–#6) và 5 dòng của sổ nhánh multi-provider (Task 1, 2, 4, 5, 6; mỗi dòng tách thành 1–3 ý, ghi số Task ở cột Nguồn). Nguồn có 83 dòng; bảng có 97 hàng vì một số dòng gộp nhiều ý được tách theo phân loại. Kết quả: 4 đã xử lý, 12 sửa trong Task 11–14, 19 chấp nhận (giới hạn), 62 để sau. Mục "để sau" ghi "chưa kiểm tra" nghĩa là chưa đối chiếu với mã hiện tại, không phải đã xác nhận còn lỗi; chúng đều là nợ nhỏ (test, dọn mã, ca hiếm), không có mục P1 nào ngoài các mục đã gán Task 11–14.

| Nguồn | Mục (rút gọn) | Phân loại | Ghi chú |
|---|---|---|---|
| Handoff 11-24, Phụ lục B, Task 1 | job_heartbeat/lock_timeout nhận 0 hoặc số âm | để sau | `core/settings.py` chỉ kiểm heartbeat×2 < lock_timeout, không chặn heartbeat ≤ 0 |
| Handoff 11-24, Phụ lục B, Task 1 | Job RUNNING có lease_expires_at NULL không bao giờ được thu hồi | sửa trong Task 11–14 | Task 11 |
| Handoff 11-24, Phụ lục B, Task 1 | Test fenced_session thiếu nhánh payload và rollback khi lỗi | để sau | Chỉ là khoảng trống test |
| Handoff 11-24, Phụ lục B, Task 1 | Test heartbeat biên thời gian hẹp (1 s / 0.2 s) trên CI tải nặng | để sau | Có thể chập chờn; chưa gặp lỗi |
| Handoff 11-24, Phụ lục B, Task 1 | Job xếp lại giữ started_at/finished_at cũ | chấp nhận (giới hạn) | Chỉ ảnh hưởng số liệu thời gian hiển thị, không ảnh hưởng đúng sai |
| Handoff 11-24, Phụ lục B, Task 1 | Test renew_lease khẳng định đúng biên 0.5 s/1.5 s | để sau | Nên thêm biên an toàn |
| Handoff 11-24, Phụ lục B, Task 2 | Test import httpx2 gián tiếp, chưa khai báo ở dev deps | để sau | `pyproject.toml` chỉ khai `httpx`; chưa kiểm tra nhóm dev |
| Handoff 11-24, Phụ lục B, Task 2 | Thêm httpx2/httpcore2 vào _QUIET_LOGGERS | để sau | `core/logging.py:30` vẫn chỉ có `httpx`, `httpcore` |
| Handoff 11-24, Phụ lục B, Task 2 | llm_effort chỉ kiểm tại thời điểm gọi | để sau | `core/settings.py:68` vẫn `dict[str, str]` không validator; chưa kiểm tra thêm |
| Handoff 11-24, Phụ lục B, Task 2 | strict_output_schema cho qua oneOf/discriminator/prefixItems | chấp nhận (giới hạn) | Giới hạn đã biết; schema W-model tránh các cấu trúc này |
| Handoff 11-24, Phụ lục B, Task 2 | Test khẳng định thuộc tính riêng của LLMClient | để sau | Nợ chất lượng test |
| Handoff 11-24, Phụ lục B, Task 3 | load_run_info ném NotFoundError mang chữ UI từ tầng pipeline | đã xử lý | Commit e469111 "keep pipeline off web layer"; `pipeline/config.py:25` nay import từ `core.errors` |
| Handoff 11-24, Phụ lục B, Task 3 | make_test_deps để httpx.AsyncClient không đóng | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 4 | Case vi_mixed_apa: listBibl ghi lại rỗng, bẫy không ghi tài liệu | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 4 | Test nhãn link không kiểm marker nằm trong văn bản | để sau | Nợ chất lượng test |
| Handoff 11-24, Phụ lục B, Task 4 | Test fixture gần tautology; conftest nạp case lúc import | để sau | Nợ chất lượng test |
| Handoff 11-24, Phụ lục B, Task 4 | Bản ghi TEI có timestamp/MD5, type-ignore trong generator | chấp nhận (giới hạn) | Bản ghi chỉ là fixture; không ảnh hưởng chạy thật |
| Handoff 11-24, Phụ lục B, Task 5 | DOCX bỏ text-box; chú thích trong bảng phân loại bằng regex | chấp nhận (giới hạn) | Giới hạn bộ đọc DOCX, chưa có ca lỗi thực tế |
| Handoff 11-24, Phụ lục B, Task 5 | parse_text_payload thiếu NO_TEXT_EXTRACTED | chấp nhận (giới hạn) | Form web đã từ chối văn bản trống; `parsing/pdf.py`/`docx.py` có mã này cho tệp |
| Handoff 11-24, Phụ lục B, Task 5 | storage.put trước kiểm lease; model_dump_json trên event loop; bộ nhớ DOCX | chấp nhận (giới hạn) | Nội dung xác định nên vô hại; trần 200 MB; chưa đo bộ nhớ |
| Handoff 11-24, Phụ lục B, Task 6 | Nhãn hỗn hợp/running header làm rơi mục trong đường pdfium; header "Bibliography" lặp chỉ giữ trang cuối | để sau | Cải thiện bộ tách tham khảo |
| Handoff 11-24, Phụ lục B, Task 6 | UnsupportedProtocol/ProxyError bị retry như lỗi tạm; log fallback thiếu error_code | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 6 | Test PDF mã hóa không đi qua worker; dòng năm bị _PAGE_NUMBER_LINE bỏ; compose chờ service_started | để sau | Chưa kiểm tra compose hiện tại |
| Handoff 11-24, Phụ lục B, Task 6 | tests/files.py import reportlab ở đầu module; _ends_entry coi "A." cuối dòng là nối tiếp | để sau | Nợ nhỏ |
| Handoff 11-24, Phụ lục B, Task 7 | S2 chạy lại join_unlabelled lên references_raw của S1 | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 7 | Dòng "Tiếng Việt."/"English." bị coi là tiêu đề nhóm | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 7 | Đường skip mất cảnh báo LLM_NOT_CONFIGURED khi retry/reuse | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 7 | Validator tác giả W1 khớp chuỗi con (do plan quy định) | chấp nhận (giới hạn) | Ca tên họ ngắn hiếm; mất khớp chỉ làm lọt cảnh báo, không bịa trích dẫn |
| Handoff 11-24, Phụ lục B, Task 7 | Assertion golden có điều kiện; báo cáo ghi sai pdf.py | để sau | Nợ chất lượng test/tài liệu |
| Handoff 11-24, Phụ lục B, Task 7 | `[n]` sau dòng trống luôn bắt đầu mục mới; `1.` sau tiêu đề lạ; cảnh báo mức tài liệu | chấp nhận (giới hạn) | Đánh đổi heuristic của bộ tách tham khảo |
| Handoff 11-24, Phụ lục B, Task 8 | Phản hồi mà mọi bản ghi bị bỏ được cache là not_found | sửa trong Task 11–14 | Task 12 |
| Handoff 11-24, Phụ lục B, Task 8 | Phản hồi S2 dict không có `data` coi là 0 kết quả | sửa trong Task 11–14 | Task 12 (cùng sửa xử lý schema lệch) |
| Handoff 11-24, Phụ lục B, Task 8 | Cache key không có schema version | sửa trong Task 11–14 | Task 12 |
| Handoff 11-24, Phụ lục B, Task 8 | Thiếu test Review Focus #5 ở gateway; docstring RateLimiter nói quá; giới hạn theo từng process | để sau | Docstring/test |
| Handoff 11-24, Phụ lục B, Task 8 | Settings thiếu openalex_api_key trong spec; 4xx không retry vẫn tính vào breaker; safe_http dùng mạng thật | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 9 | Lỗi không cô lập không dừng các lookup còn lại; domain_flag bị ghi đè; race xóa sample | để sau | Tốn quota, chưa gây sai kết quả |
| Handoff 11-24, Phụ lục B, Task 9 | check_url coi 403/429 là link chết | sửa trong Task 11–14 | Task 12 |
| Handoff 11-24, Phụ lục B, Task 9 | Test helper import chéo, test concurrency dựa sleep, Focus #4 còn khoảng trống, migration 0008 trùng DOI khác hoa thường | để sau | Nợ test và ca lý thuyết |
| Handoff 11-24, Phụ lục B, Task 9 | Nguồn lỗi một phần xuất hiện ở cả searched và failed; văn bản NOT_FOUND nói quá độ phủ | để sau | Nguồn ghi văn bản NOT_FOUND hơi nói quá mức độ bao phủ; chưa sửa |
| Handoff 11-24, Phụ lục B, Task 9 | Tham khảo dài có phụ đề, không năm/tác giả, bị loại so với ứng viên ngắn | chấp nhận (giới hạn) | Heuristic khớp thận trọng: bỏ sót an toàn hơn khớp sai |
| Handoff 11-24, Phụ lục B, Task 10 | Tỉ lệ `[1:2]`/`[10:30]` bị đọc là trích dẫn | chấp nhận (giới hạn) | Đã ghi nhận "Hạn chế đã biết" ở spec §7.4 (`docs/superpowers/specs/2026-11-24-core-pipeline-design.md:230`) |
| Handoff 11-24, Phụ lục B, Task 10 | Downgrade 0009 mất dữ liệu không có test; `Tr.` phân biệt hoa thường; `[3]–[2]`; "No." | để sau | Hiếm gặp |
| Handoff 11-24, Phụ lục B, Task 10 | Token toàn viết tắt ("p.p.") nuốt ranh giới câu; `[2019, tr. 5, 3, tr. 7]` giải sai | chấp nhận (giới hạn) | Heuristic tách marker; ca rất hiếm |
| Handoff 11-24, Phụ lục B, Task 11 | Suy biến W2 im lặng khi lần thử cuối lỗi | để sau | Nên thêm cảnh báo như S2; chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 11 | Nhánh ngoại lệ W2 không test; test_demo nới COMPLETED_WITH_WARNINGS; gọi tuần tự; tách câu thừa | để sau | Nợ test/hiệu năng |
| Handoff 11-24, Phụ lục B, Task 12 | Work không có bản mở bị truy vấn lại mỗi run; abstract đã lưu không làm mới | để sau | Chỉ tốn thời gian/quota OA, không sai kết quả |
| Handoff 11-24, Phụ lục B, Task 12 | Ngày consent trùng ngày ship; .env.example ghim ngày; consent chỉ kiểm ở tầng web | để sau | Chưa kiểm tra sau nhánh multi-provider (re-consent đã thêm) |
| Handoff 11-24, Phụ lục B, Task 12 | Ghi đồng thời chưa test; file mồ côi; redirect không giới hạn tốc độ; fallback ArxivAccess chỉ khi arxiv_id rỗng | để sau | Chưa kiểm tra |
| Handoff 11-24, Phụ lục B, Task 12 | Test helper import chéo, AsyncClient không đóng, ghi 48 MB, WORK_CONCURRENCY cứng, props test-only | để sau | Nợ test |
| Handoff 11-24, Phụ lục B, Task 13 | Bộ tách câu chunker lệch với citations/sentences.py | để sau | Điểm cắt đã đóng băng; thống nhất ở `citeproof.parsing` sau |
| Handoff 11-24, Phụ lục B, Task 13 | Cửa sổ ≥250 token không tổng quát; race parsed-JSON; thiếu unique chunk_index; GROBID outage không dừng sớm | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 14 | numbers_preserved khớp chuỗi con ("12" trong "2012") | sửa trong Task 11–14 | Task 13 |
| Handoff 11-25, Task 14 | test_lexical chấp nhận hai thứ tự; bad_output không khẳng định W3; STOPWORDS trùng | để sau | Nợ test |
| Handoff 11-25, Task 14 | Cặp không có hit không ghi dòng, bị xử lý lại khi resume | chấp nhận (giới hạn) | Task 13: chạy lại vô hại vì truy xuất lexical tất định và bản dịch W3 lấy từ cache LLM; S8 xử lý cặp 0 kết quả và cặp không có dòng `retrieval_results` như nhau (`EVIDENCE_INCONCLUSIVE` tất định, không gọi LLM), xem `test_s7_zero_hits_same_as_pair_without_retrieval_rows` |
| Handoff 11-25, Task 14 | retrieve() giữ session kết nối lười qua lúc gọi W3 | để sau | Bất biến chỉ nằm trong chú thích |
| Handoff 11-25, Task 14 | test_search_finds_decimal có replace vô tác dụng; số thập phân sinh thêm phần nguyên/phân số | chấp nhận (giới hạn) | Nhiễu truy xuất nhỏ |
| Handoff 11-25, Task 15 | Quote có thể kết thúc ở ký tự gốc trước dấu kết hợp (không phải tiếng Việt) | chấp nhận (giới hạn) | Ngoài phạm vi ngôn ngữ v1 |
| Handoff 11-25, Task 15 | Quote khớp chuỗi con không theo ranh giới từ ("95.7%" trong "195.7%") | sửa trong Task 11–14 | Task 13 |
| Handoff 11-25, Task 15 | Downgrade giữ lời LLM có thể mâu thuẫn; _NOTES trùng rules.py | để sau | Plan quy định downgrade_note |
| Handoff 11-25, Task 16 | Cuộc gọi đang bay vượt trần chi phí khi concurrency>1 | sửa trong Task 11–14 | Task 13 |
| Handoff 11-25, Task 16 | Retry schema LLMClient không bị chặn; COST_LIMIT_REACHED mất nếu lần chạm trần lỗi | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 16 | Thay từ cấm làm mất downgrade note; wrapper source_chunks viết tay; trùng vòng ưu tiên; import LLM_NOT_CONFIGURED | để sau | Dọn mã |
| Handoff 11-25, Task 17 | Vòng lặp vẫn truy xuất sau khi chạm trần; SubClaim thiếu extra=forbid; test từ cấm rỗng; nhiều nợ nhỏ khác | để sau | Chưa kiểm tra từng mục |
| Handoff 11-25, Task 17 | Chính sách lỗi W3 của V3 khác S7; nhánh lỗi V3 chưa test | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 18 | Việc cần làm "missing" đếm cả REFERENCE_NOT_FOUND; config lưu sai gây báo cáo 500; test chưa đủ | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 19 | Upload thứ hai giữ dòng/tệp cũ ẩn; tệp mồ côi nếu commit lỗi; chữ inline; FILE_TOO_LARGE trùng | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 19 | services/sources.py import web.labels (phân lớp) | để sau | Vẫn còn: `services/sources.py:36 from citeproof.web import labels`; Task 3 đã sửa ở pipeline nhưng không phải ở đây |
| Handoff 11-25, Task 20 | Đọc status trước lock; test merge thiếu; is_new so now() đầu giao dịch; progress hiển thị skipped là DONE | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 21 | _finalize_if_idle chứa tri thức DAG; _drive không deadline; eval dùng chung storage_dir; hòa khi cross-file | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 21 | V2 mọi hit bằng 0 báo metric None thay vì 0 | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 22 | 429 không có security headers | sửa trong Task 11–14 | Task 14 |
| Handoff 11-25, Task 22 | _prune O(n); thiếu test XFF; settings rate thiếu ge=1; bucket "unknown" dùng chung | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 22 | Regex progress dùng match+$ (nhận "\n" cuối) | để sau | fullmatch chặt hơn; chưa sửa |
| Handoff 11-25, Task 23 | smoke --password lộ trong argv | sửa trong Task 11–14 | Task 14 |
| Handoff 11-25, Task 23 | CHANGE_ME_ không bị từ chối khi khởi động prod | sửa trong Task 11–14 | Task 14 |
| Handoff 11-25, Task 23 | Thiếu citeproof_eval làm backup dừng; backup lỗi bị nuốt im lặng; citeproof_test tạo ở prod; test deploy thiếu ca | để sau | Chưa kiểm tra từng mục; `scripts/backup.sh` |
| Handoff 11-25, Task 23 | Ví dụ "Thử khôi phục" thiếu sudo; test deploy chỉ kiểm chuỗi có mặt | để sau | `docs/14-deployment-devops.md:110` đã nói cần sudo nhưng ví dụ dòng 112 chưa có |
| Handoff 11-25, Task 24 | gather không hủy tool anh em; LAST_CALL_TEXT lặp; cache key bước thiếu effort/max_tokens; logic trùng LLMClient | để sau | Cache key là quy định plan; chưa kiểm tra phần còn lại |
| Handoff 11-25, Task 25 | PARSE_FAILED test bag-of-words; agent_run_id mất; truncated không đặt; agent input thiếu top-3 | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 25 | venues_equal nhận series một chữ cái; venue_in_raw một chiều; DOI regex tham lam | chấp nhận (giới hạn) | Luôn gắn cờ METADATA_MISMATCH hoặc false negative an toàn (theo ghi chú nguồn) |
| Handoff 11-25, Task 26 | V4 bỏ qua enabled; READ_LIMIT_EXCEEDED không phát; cắt đo trên chữ thô; S7 chạy cho V4 | để sau | Chưa kiểm tra |
| Handoff 11-25, Task 26 | _latest_agent_run có thể chọn hàng ERROR của lần thử trước; import trong hàm | để sau | Chưa kiểm tra |
| Handoff 11-25, Final review | Parked #3: run cha vẫn mời kiểm chứng lại sau khi run con dùng upload | chấp nhận (giới hạn) | Phán quyết: chi phí ≈ 0 nhờ cache LLM; tệ nhất là run con trùng |
| Handoff 11-25, Final review | Parked #4: refusal fallback theo prefix model, không theo task | để sau | Đúng với cấu hình mặc định; ghi lại ở đây, sửa khi đổi mặc định |
| Handoff 11-25, Final review | Parked #5: token cache tính theo giá input đầy đủ | chấp nhận (giới hạn) | Ước cao có chủ đích (phán quyết Task 2); trần chi phí chạm hơi sớm |
| Handoff 11-25, Final review | Parked #6: migration 0010 unique index không khử trùng lặp | sửa trong Task 11–14 | Task 11 |
| Handoff 11-25, Final review | Retention backup bị bỏ khi dump lỗi; guard discard không khóa dòng document; chữ RUN_IN_PROGRESS inline | để sau | Chưa kiểm tra |
| nhánh multi-provider, Task 1 | bảng phase của project-status cũ; env_file brittle | đã xử lý | Commit 5a75633 "load .env in app containers; refresh stale docs" |
| nhánh multi-provider, Task 1 | README dùng `echo ... >> .env`, chạy lại sẽ trùng dòng | để sau | `README.md:35` vẫn dùng `>> .env` |
| nhánh multi-provider, Task 2 | llm_models override một phần giữ tên Claude trên openai_compatible | đã xử lý | Commit 3c904f3 "require every task model when LLM is configured"; docs b6edea8 |
| nhánh multi-provider, Task 2 | cổng 0 bị bỏ qua | chấp nhận (giới hạn) | Cổng 0 không hợp lệ cho dịch vụ LLM; negligible |
| nhánh multi-provider, Task 4 | input None → "null"; không chặn tool_result mồ côi; trùng cảnh báo empty_choices | để sau | Chưa kiểm tra |
| nhánh multi-provider, Task 5 | ADR-011 còn todo "cập nhật 07 §4.2"; bảng 07 §4.2 một provider | để sau | Chưa kiểm tra; Task 9 có phạm vi tài liệu này |
| nhánh multi-provider, Task 5 | LLMProvider protocol thiếu aclose | đã xử lý | `src/citeproof/llm/client.py:37` nay có `aclose` trong protocol; provider đều cài `aclose` |
| nhánh multi-provider, Task 5 | contract roundtrip yếu; client SDK không đóng trong test_factory; import private; không chạy RED | để sau | Nợ test |
| nhánh multi-provider, Task 6 | run tạo trước nhánh có provider=None, không bị chặn | chấp nhận (giới hạn) | Phán quyết người điều phối: chưa có run LLM thật trước đây |
| nhánh multi-provider, Task 6 | _provider_changed dựng RunConfig đầy đủ; test freeze DB yếu; trang overview không có thông báo | để sau | Chưa kiểm tra |

---

## 6. Kết quả sau khi làm

Nhánh `feat/multi-provider-completion` (từ `main`, 27/11/2026). Số liệu chạy lại cuối Task 16.

### 6.1 Kiểm tra mới

| Lệnh | Kết quả |
|---|---|
| `make check` | ruff `All checks passed!`; `382 files already formatted`; mypy `Success: no issues found in 196 source files` |
| `CITEPROOF_REQUIRE_DB=1 uv run pytest -q --cov=citeproof --cov-report=term` | **1856 passed, 3 skipped** trong 170,55 s; `TOTAL 11010 447 96%` (coverage 96%) |
| 3 test skip | Test live LLM (`tests/llm/test_llm_live.py`, 2 mục, cần `CITEPROOF_LIVE_LLM=1`) và GROBID live (`tests/integrations/test_grobid.py`, cần `CITEPROOF_LIVE_GROBID=1`) |
| GROBID live: `docker compose up -d grobid`, `curl -s localhost:8070/api/isalive` → `true`, rồi `CITEPROOF_LIVE_GROBID=1 uv run pytest -m grobid -q` | `1 passed, 1858 deselected in 17.40s` |
| LLM live (`CITEPROOF_LIVE_LLM=1 uv run pytest -m live -q`) | **chưa chạy: chưa cấu hình LLM** (`Settings().llm_configured` in `False`) |
| Một bài thật qua UI, đo chi phí thật | **chưa chạy: chưa cấu hình LLM** |

### 6.2 Từng mục audit

| Mục | Task / commit | Trạng thái | Kiểm chứng bằng |
|---|---|---|---|
| A-1 compose không nhận key | Task 1 `5a75633` | Đã sửa | `uv run pytest tests/deploy -q`; `docker compose config -q` |
| A-2 README sai/thiếu | Task 1 `5a75633`, Task 9 `b6edea8` | Đã sửa | Đọc `README.md` mục "Bật LLM" |
| A-3 project-status cũ | Task 16 | Đã làm mới | `docs/project-status.md` |
| A-4 chú thích `feat/core-pipeline` | Task 1 `5a75633` | Đã sửa chú thích; xóa/đẩy nhánh cũ vẫn chờ người dùng | `grep -n "feat/core-pipeline" docs/task-tracking.md` |
| A-5 coverage | Task 16 | Đã làm: CI chạy `--cov=citeproof --cov-report=term`, không đặt ngưỡng | `uv run pytest --cov=citeproof --cov-report=term` (TOTAL 96%) |
| A-6 chạy live | Task 16 | **Một phần**: GROBID live đạt; LLM live và bài thật qua UI chưa chạy (chưa cấu hình LLM) | `docs/live-check.md` |
| §3.2 T-604 HTML in được | Task 15 `9b990ba`, `b535443` | Đã xong (COMPLETED) | `uv run pytest tests/web tests/modules -q -k report` |
| §3.5 P1: migration 0010 trùng lặp | Task 11 `5f7df9f`, `44020b8` | Đã sửa | `CITEPROOF_REQUIRE_DB=1 uv run pytest tests/db -q` |
| §3.5 P1: job RUNNING lease NULL | Task 11 `5f7df9f` | Đã sửa | `CITEPROOF_REQUIRE_DB=1 uv run pytest tests/pipeline tests/db -q` |
| §3.5 P1: cache metadata all-dropped, key không có version | Task 12 `e45defd` | Đã sửa | `uv run pytest tests/modules -q` |
| §3.5 P1: `check_url` coi 403/429 là chết | Task 12 `e45defd` | Đã sửa (trả "không rõ") | `uv run pytest tests/modules -q` |
| §3.5 P1: cặp không truy xuất được | Task 13 | Chấp nhận (giới hạn), có test | `uv run pytest tests/modules -q -k s7_zero_hits` |
| §3.5 P1: số/quote không theo ranh giới | Task 13 `6d0475d` | Đã sửa (còn biên "95" trong "95.7%", ghi bên dưới) | `uv run pytest tests/modules -q` |
| §3.5 P1: chi phí vượt trần khi concurrency > 1 | Task 13 `6d0475d`, `25f2811` | Đã sửa; giá cache token vẫn ước cao có chủ đích | `uv run pytest tests/modules -q` |
| §3.5 P1: 429 thiếu security headers | Task 14 `3dff9fe` | Đã sửa | `uv run pytest tests/web tests/deploy -q` |
| §3.5 P1: `smoke.py --password`, `CHANGE_ME_` | Task 14 `3dff9fe` | Đã sửa (`scripts/ui_snapshots.py` vẫn nhận `--password`) | `uv run pytest tests/deploy -q` |
| Multi-provider: provider, factory, OpenAI-compatible, agent turn | Task 2–5 `f8551c9`, `1705f20`, `bbb3958`, `8724d26`, `b4f4113` | Đã xong | `uv run pytest tests/llm -q` |
| Multi-provider: đóng băng provider theo run, chặn reverify/resume | Task 6 `1ffd010`, `e469111` | Đã xong | `CITEPROOF_REQUIRE_DB=1 uv run pytest tests -q -k provider` |
| Multi-provider: đồng ý lại khi đổi provider | Task 7 `f7a6140`, `7773ee1` | Đã xong | `uv run pytest tests/web -q -k consent` |
| Multi-provider: eval trên mọi provider | Task 8 `36cae3b`, `abf0e21` | Đã xong | `uv run pytest tests/evaluation -q` |
| Multi-provider: tài liệu cấu hình, yêu cầu đủ 8 model | Task 9 `b6edea8`, `3c904f3` | Đã xong | `uv run pytest tests/core -q` |
| Phân loại việc hoãn (§5) | Task 10 `9eb07a7`, `10722a2` | Đã xong | Đọc §5 |

### 6.3 Còn lại (ngoài phạm vi mã hoặc cần bạn)

- **Live LLM**: chưa chạy (chưa cấu hình LLM); cần API key hoặc model cục bộ, rồi làm theo `docs/live-check.md` (b), (d). Đầu vào cho T-008/T-703.
- **Q-03**: tên và link chính sách dữ liệu nhà cung cấp LLM trên màn hình đồng ý (chặn pilot).
- **Việc hoãn ở §5**: 62 hàng "để sau" (nợ test, dọn mã, ca hiếm; nhiều mục "chưa kiểm tra"); 19 hàng chấp nhận giới hạn.
- **Nghiên cứu, dữ liệu, thực nghiệm, pilot**: T-001…T-010, T-E01…T-E07, T-701…T-703, T-705, T-306, T-407, T-607, T-801, T-802, T-901, T-902; các task IN_PROGRESS chờ dữ liệu/vận hành: T-203, T-208, T-302, T-408, T-506, T-606, T-704; T-202 (S3) hoãn.
- **Việc nhỏ hoãn từ sổ nhánh này**: `llm_models` một phần đã được siết bằng validator (Task 9); còn `input None → "null"` và tool_result mồ côi (Task 4); test contract/factory yếu và client SDK không đóng (Task 5); run tạo trước nhánh có `provider=None` không bị chặn (Task 6); trang overview chưa có thông báo đổi provider; biên số/quote "95" trong "95.7%" và "234" trong "1,234"; giá `verdict.judge` dùng cho closed-book khi đặt chỗ chi phí (Task 13); lỗi all-items-lack-title của Crossref nay là lỗi thay vì not_found, và key bump làm mồ côi cache metadata eval cũ (Task 12); comment sai về 413 trong Task 14, `CITEPROOF_DOMAIN`/`POSTGRES_PASSWORD` placeholder chưa quét.
- **Nhánh `feat/core-pipeline`**: có xóa/đẩy hay không vẫn chờ bạn. Container `citeproof-grobid-1` vẫn đang chạy sau Task 16.

