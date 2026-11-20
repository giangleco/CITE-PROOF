# 12 — Development Guidelines: CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 5 — Development Preparation |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | Bản nháp trước |
| Tài liệu liên quan | `07-technical-architecture.md`, `13-testing-strategy.md` |

---

## 1. Overview

Quy ước phát triển cho một người làm: cấu trúc mã nguồn, chuẩn code, Git, quản lý phụ thuộc, cấu hình, logging, xử lý lỗi, quy tắc dùng LLM và CI. Mục tiêu là giữ chất lượng bằng công cụ tự động thay vì phụ thuộc vào trí nhớ.

## 2. Objectives

- Cấu trúc mã nguồn phản ánh các module ở `07` §4.4.
- Mọi kiểm tra chất lượng chạy tự động trong CI.
- Mọi lời gọi LLM đi qua một chỗ, có phiên bản và có log.

## 3. Scope

Mã nguồn backend, worker, giao diện pilot, prompt, mã đánh giá.

## 4. Detailed Analysis

### 4.1 Cấu trúc repository

Đơn giản hóa từ v1.0 §19:

```text
citeproof-ai/
├── src/citeproof/
│   ├── core/            # settings, logging, lỗi, domain model, enum
│   ├── api/             # router FastAPI, schema request/response
│   ├── web/             # giao diện pilot: template Jinja2, static
│   ├── modules/
│   │   ├── documents/
│   │   ├── references/
│   │   ├── citations/
│   │   ├── sources/
│   │   ├── retrieval/
│   │   ├── verification/
│   │   └── reports/
│   ├── pipeline/        # giai đoạn S1–S9, runner, jobs
│   ├── llm/             # LLMClient, nạp prompt, cache
│   ├── integrations/    # crossref, openalex, semantic_scholar, unpaywall, arxiv, grobid
│   ├── storage/         # object storage
│   └── db/              # model SQLAlchemy, repository
├── prompts/             # <task>/<version>.md
├── migrations/          # Alembic
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── api/
│   ├── e2e/
│   └── fixtures/        # PDF/DOCX mẫu, phản hồi HTTP đã ghi, phản hồi LLM đã ghi
├── benchmark/
│   ├── datasets/        # D1–D5 (không commit dữ liệu có giới hạn giấy phép)
│   ├── annotations/
│   ├── configs/         # cấu hình biến thể
│   ├── results/
│   └── scripts/
├── docs/
├── docker/
├── docker-compose.yml
├── pyproject.toml
├── Makefile
└── .env.example
```

### 4.2 Chuẩn code

| Quy tắc | Chi tiết |
|---|---|
| Format và lint | Ruff (thay cho flake8, isort, black) |
| Kiểu | Type hint đầy đủ; mypy chế độ strict cho `core`, `modules`, `pipeline` |
| Ranh giới dữ liệu | Pydantic ở mọi ranh giới: API, đầu ra LLM, giữa các giai đoạn pipeline |
| Router | Không chứa logic nghiệp vụ; chỉ gọi tầng ứng dụng |
| Phụ thuộc giữa module | Theo bảng ở `07` §4.4; `integrations` chỉ được dùng qua interface ở `09` §4.6 |
| Async | Dùng async cho I/O; không gọi hàm chặn trong vòng lặp sự kiện |
| Đặt tên | Tiếng Anh cho code; tiếng Việt cho nội dung giao diện và tài liệu |
| Docstring | Bắt buộc cho hàm công khai của module |

### 4.3 Git workflow

| Quy tắc | Chi tiết |
|---|---|
| Mô hình | Trunk-based: `main` luôn chạy được; nhánh ngắn `feat/T-xxx-...`, `fix/...`, `docs/...` |
| Commit | Conventional Commits, ví dụ `feat(references): add metadata matching (T-302)` |
| Merge | Qua pull request để CI chạy; tự review bằng checklist §4.9 |
| Tag | Gắn tag khi xong milestone, ví dụ `m3-pilot-alpha` |
| Không commit | Bí mật, dữ liệu người dùng, dataset có giới hạn giấy phép |

### 4.4 Quản lý phụ thuộc `[ĐX]`

- Dùng một công cụ có lock file (ví dụ uv hoặc Poetry), chốt khi làm T-101.
- Khóa phiên bản trong lock file; cập nhật định kỳ theo `15`.
- Phiên bản Python cố định trong `pyproject.toml` và Dockerfile.

### 4.5 Cấu hình

- Cấu hình bằng biến môi trường, đọc qua pydantic-settings.
- `.env.example` liệt kê mọi biến, không có giá trị bí mật.
- Cấu hình biến thể thực nghiệm dùng file trong `benchmark/configs/`, không dùng biến môi trường.

### 4.6 Logging

| Quy tắc | Chi tiết |
|---|---|
| Định dạng | JSON có cấu trúc |
| Trường bắt buộc | `timestamp`, `level`, `module`, `event`, và `run_id` / `document_id` khi có |
| Cấm ghi | Văn bản tài liệu, nội dung nguồn, prompt đầy đủ, mật khẩu, token |
| Mức | `DEBUG` chỉ dùng local; `INFO` cho sự kiện giai đoạn; `WARNING` cho kết quả bị hạ mức; `ERROR` cho lỗi cần xử lý |

### 4.7 Xử lý lỗi

```text
CiteProofError
├── InputError              # người dùng gửi dữ liệu sai → 4xx
├── ParseError              # không parse được tài liệu hoặc nguồn
├── ExternalServiceError    # API ngoài lỗi
│   └── RetryableError      # timeout, 429, 5xx → thử lại có backoff
├── LLMOutputError          # đầu ra sai schema sau số lần thử tối đa
└── InternalError           # lỗi lập trình → 500, ghi log ERROR
```

- Lỗi trong một tài liệu tham khảo hay một claim không làm hỏng cả lần chạy: ghi trạng thái `PROCESSING_ERROR` cho mục đó và tiếp tục.
- Lỗi ở cấp giai đoạn làm dừng lần chạy ở giai đoạn đó, cho phép tiếp tục sau (NFR-04).

### 4.8 Quy tắc dùng LLM

1. Mọi lời gọi đi qua `LLMClient`; không gọi SDK nhà cung cấp trực tiếp từ module nghiệp vụ.
2. Prompt nằm trong `prompts/`, có phiên bản; không viết prompt trong code.
3. Chỉ dùng đầu ra có cấu trúc, validate bằng Pydantic.
4. Mỗi lời gọi ghi model, phiên bản prompt, token, chi phí, cache hit.
5. Đổi prompt mặc định phải chạy lại đánh giá trên tập dev và ghi kết quả.
6. Không tinh chỉnh prompt dựa trên tập test.

### 4.9 Checklist tự review trước khi merge

- [ ] Task có ID và acceptance criteria đã đạt.
- [ ] Có test cho logic mới; CI đạt.
- [ ] Không có bí mật, dữ liệu người dùng hay nội dung tài liệu trong code, log, fixture.
- [ ] Thay đổi lược đồ có migration.
- [ ] Thay đổi contract có cập nhật `09` và test.
- [ ] Thay đổi kiến trúc có ADR.
- [ ] `progress/task-tracking.md` đã cập nhật.

### 4.10 CI

| Bước | Công cụ |
|---|---|
| Lint, format | Ruff |
| Kiểu | mypy |
| Test | pytest (unit, API, integration với service container PostgreSQL) |
| Bí mật | Công cụ quét bí mật `[ĐX]` |
| Lỗ hổng phụ thuộc | Công cụ quét phụ thuộc `[ĐX]` |
| Build | Build Docker image khi gắn tag |

Test gọi LLM thật và test gọi API bên ngoài thật không chạy trong CI mặc định; chúng chạy thủ công bằng một lệnh riêng.

## 5. Implementation Steps

- [ ] Chốt công cụ quản lý phụ thuộc và công cụ quét trong CI.
- [ ] Tạo repo theo §4.1 (T-101).
- [ ] Thiết lập pre-commit và CI (T-101, T-106).
- [ ] Viết `Makefile` với các lệnh: `check`, `test`, `up`, `migrate`, `eval`.

## 6. Expected Outputs

Repo khởi tạo đúng cấu trúc; CI chạy; quy ước được áp dụng từ commit đầu tiên.

## 7. Dependencies

`07` (module), `09` (interface), `13` (test).

## 8. Risks and Considerations

Làm một mình dễ bỏ qua review. Checklist §4.9 và CI là hàng rào chính; không merge khi CI đỏ, kể cả khi gấp.

## 9. Acceptance Criteria

- [ ] Repo khởi tạo theo §4.1.
- [ ] CI chạy đủ các bước §4.10.
- [ ] Có `.env.example` và `Makefile`.

## 10. References

- CiteProof AI Project Documentation v1.0: §5.2, §19, §23.
