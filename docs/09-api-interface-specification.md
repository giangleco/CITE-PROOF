# 09 — API & Interface Specification: CiteProof AI (MVP)

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 3 — System Architecture & Technical Design |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | Bản nháp trước; mức chi tiết đủ cho MVP (D-09) |
| Tài liệu liên quan | `04-prd.md`, `07-technical-architecture.md`, `08-database-design.md` |

---

## 1. Overview

Đặc tả REST API công khai cho giao diện pilot và các interface nội bộ quan trọng giữa các module. Giao diện server-rendered (ADR-005) gọi trực tiếp tầng ứng dụng, nhưng dùng cùng các schema này để sau này front-end riêng có thể dùng lại.

## 2. Objectives

- Định nghĩa endpoint đáp ứng FR P0.
- Định nghĩa data contract bằng Pydantic, dùng chung cho API, pipeline và đánh giá.
- Định nghĩa interface nội bộ để thay thế được thành phần (connector, verifier, LLM client).

## 3. Scope

REST API v1 cho MVP; interface nội bộ chính. Ngoài phạm vi: API công khai cho bên thứ ba.

## 4. Detailed Analysis

### 4.1 Quy ước chung

| Mục | Quy ước |
|---|---|
| Tiền tố | `/api/v1` |
| Định dạng | JSON; upload dùng `multipart/form-data` |
| Xác thực | Cookie phiên (giao diện) hoặc header `Authorization: Bearer` (công cụ), theo §4.2 |
| ID | UUID |
| Thời gian | ISO 8601, UTC |
| Phân trang | `?limit=&cursor=`; phản hồi có `next_cursor` |
| Ngôn ngữ thông báo | Theo header `Accept-Language` (`vi` mặc định) |

**Định dạng lỗi**

```json
{
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "Tệp vượt quá giới hạn kích thước cho phép.",
    "details": {"max_bytes": 0}
  }
}
```

| HTTP | Mã lỗi ví dụ |
|---|---|
| 400 | `INVALID_INPUT`, `UNSUPPORTED_FILE_TYPE` |
| 401 / 403 | `UNAUTHENTICATED`, `FORBIDDEN`, `CONSENT_REQUIRED` |
| 404 | `NOT_FOUND` (cũng dùng khi tài nguyên thuộc người khác, để tránh lộ sự tồn tại) |
| 409 | `RUN_IN_PROGRESS` |
| 413 | `FILE_TOO_LARGE` |
| 429 | `RATE_LIMITED`, `QUOTA_EXCEEDED` |
| 500 | `INTERNAL_ERROR` |

### 4.2 Xác thực cho pilot `[ĐX]`

Đăng ký bằng mã mời và mật khẩu; mật khẩu băm theo `10`. Phương án thay thế: đăng nhập bằng liên kết qua email (cần dịch vụ gửi mail) hoặc OAuth. Chốt ở ADR riêng nếu chọn khác.

### 4.3 Endpoint

| Method | Endpoint | Mô tả | FR |
|---|---|---|---|
| POST | `/auth/register` | Đăng ký bằng mã mời | FR-60 |
| POST | `/auth/login`, `/auth/logout` | Đăng nhập, đăng xuất | FR-60 |
| POST | `/me/consent` | Ghi nhận đồng ý điều khoản dữ liệu | FR-63 |
| DELETE | `/me` | Xóa tài khoản và dữ liệu | FR-62 |
| POST | `/documents` | Tải lên PDF/DOCX | FR-01 |
| POST | `/documents/text` | Gửi đoạn văn kèm danh mục tài liệu tham khảo | FR-02 |
| GET | `/documents` | Danh sách tài liệu của người dùng | — |
| GET | `/documents/{id}` | Chi tiết tài liệu, cảnh báo parse | FR-03, FR-04 |
| DELETE | `/documents/{id}` | Xóa tài liệu và dữ liệu liên quan | FR-62 |
| POST | `/documents/{id}/runs` | Tạo lần chạy kiểm chứng | US-01 |
| GET | `/runs/{run_id}` | Trạng thái, giai đoạn, tiến độ | NFR-01 |
| GET | `/runs/{run_id}/references` | Kết quả kiểm tra tài liệu tham khảo | FR-12–FR-15 |
| GET | `/runs/{run_id}/claims` | Kết quả claim; lọc `?status=&reference_id=` | FR-40, FR-52 |
| GET | `/claim-verifications/{id}` | Chi tiết, kèm bằng chứng | FR-41, FR-51 |
| GET | `/runs/{run_id}/missing-sources` | Tài liệu được trích còn thiếu toàn văn | FR-32 |
| POST | `/reference-entries/{id}/source-file` | Tải lên tài liệu được trích | FR-32, FR-33 |
| POST | `/runs/{run_id}/reverify` | Kiểm chứng lại các claim liên quan file mới | FR-34 |
| GET | `/runs/{run_id}/report?format=html\|md` | Báo cáo | FR-50, FR-53 |
| POST | `/feedback` | Gửi phản hồi cho một kết quả | FR-70 |
| GET | `/health` | Kiểm tra sức khỏe dịch vụ | — |

Tiến độ được cập nhật bằng polling `GET /runs/{run_id}` trong MVP `[ĐX]`; Server-Sent Events để sau.

### 4.4 Data contract chính

**Run**

```json
{
  "id": "uuid",
  "document_id": "uuid",
  "status": "RUNNING",
  "current_stage": "S3",
  "stages": [
    {"stage": "S1", "status": "DONE"},
    {"stage": "S2", "status": "DONE"},
    {"stage": "S3", "status": "RUNNING", "progress": {"done": 12, "total": 40}}
  ],
  "warnings": [],
  "created_at": "2026-11-20T08:00:00Z"
}
```

Giá trị `status`: `QUEUED`, `RUNNING`, `COMPLETED`, `COMPLETED_WITH_WARNINGS`, `FAILED`, `CANCELLED`.

**ReferenceCheck**

```json
{
  "reference_entry_id": "uuid",
  "raw_text": "Nguyễn V. A. (2020). ...",
  "ref_type": "ARTICLE",
  "status": "METADATA_MISMATCH",
  "mismatches": [
    {"field": "year", "in_document": "2020", "found": "2019"}
  ],
  "sources_searched": ["crossref", "openalex", "semantic_scholar"],
  "matched_work": {"doi": "10.xxxx/xxxxx", "title": "..."}
}
```

**ClaimVerification**

```json
{
  "id": "uuid",
  "claim": {"text": "...", "sentence": "...", "page": 12, "language": "vi"},
  "reference_entry_id": "uuid",
  "status": "PARTIALLY_SUPPORTED",
  "unverifiable_reason": null,
  "content_level_used": "FULL_TEXT",
  "explanation": "Nguồn báo cáo kết quả trên một tập dữ liệu, trong khi nhận định khái quát cho mọi tập dữ liệu.",
  "limitations": "Chỉ xét chính tài liệu được trích.",
  "needs_user_review": true,
  "evidence": [
    {
      "relation": "PARTIALLY_SUPPORTS",
      "quote": "…đoạn nguyên văn từ nguồn…",
      "page": 7,
      "section": "5 Experiments",
      "source": {"doi": "10.xxxx/xxxxx", "url": "https://…", "origin": "OPEN_ACCESS"}
    }
  ],
  "model": "…",
  "prompt_version": "judge/v3"
}
```

**Verdict v2** — đầu ra LLM ở giai đoạn S8 (W5 và `submit_verdict` của A2), trước khi áp quy tắc ở `07` §4.6. Đặc tả đầy đủ ở `07a` §4.6.3.

```json
{
  "schema_version": "verdict/2",
  "status": "SUPPORTED | PARTIALLY_SUPPORTED | CONTRADICTED | UNSUPPORTED | UNVERIFIABLE",
  "evidence": [{"chunk_id": "chunk_3", "quote": "…", "relation": "SUPPORTS | PARTIALLY_SUPPORTS | CONTRADICTS | RELATED"}],
  "abstain_cause": "TEXT_UNREADABLE | CONTEXT_INCONCLUSIVE | null",
  "explanation": "…",
  "limitations": "…"
}
```

- `chunk_id` là ID ngắn trong context (`chunk_1`…). Server ánh xạ về UUID của `source_chunks`.
- `abstain_cause` chỉ có khi `status = UNVERIFIABLE`. Ánh xạ: `TEXT_UNREADABLE` → `SOURCE_PARSE_FAILED`; `CONTEXT_INCONCLUSIVE` → `EVIDENCE_INCONCLUSIVE`.
- `content_level_used`, `unverifiable_reason`, `needs_user_review` do code gán.

Trong `ReferenceCheck`, thêm `needs_user_review` và `sources_failed`. Trạng thái `LOOKUP_FAILED` có `sources_searched` rỗng.

### 4.5 Giới hạn `[ĐX]`

Các giá trị cụ thể chốt sau SPK-05:

| Giới hạn | Áp dụng cho |
|---|---|
| Kích thước file, số trang tối đa | `POST /documents`, `POST /reference-entries/{id}/source-file` |
| Số claim tối đa mỗi lần chạy | `POST /documents/{id}/runs` |
| Số lần chạy mỗi ngày mỗi người dùng | Chống lạm dụng chi phí (NFR-08) |
| Số yêu cầu mỗi phút | Toàn API |

### 4.6 Interface nội bộ

Các protocol dưới đây là hợp đồng giữa module; triển khai cụ thể có thể thay thế.

```python
from typing import Protocol, TypeVar
from uuid import UUID
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class MetadataConnector(Protocol):
    name: str
    async def search(self, ref: "ParsedReference", limit: int = 5) -> list["WorkCandidate"]: ...
    async def get_by_doi(self, doi: str) -> "WorkRecord | None": ...

class AccessResolver(Protocol):
    async def resolve(self, work: "WorkRecord") -> list["AccessOption"]: ...

class FullTextFetcher(Protocol):
    async def fetch(self, option: "AccessOption") -> "FetchedFile | None": ...

class LLMClient(Protocol):
    async def complete_structured(
        self, task: str, prompt_version: str, inputs: dict, schema: type[T]
    ) -> "LLMResult[T]": ...  # tự xử lý cache, retry, ghi llm_calls

class EvidenceRetriever(Protocol):
    async def retrieve(
        self, claim: "Claim", source_file_id: UUID, k: int, mode: "CrossLingualMode"
    ) -> list["ScoredChunk"]: ...

class Verifier(Protocol):
    variant: str  # V0..V4, "V2+A2"
    async def verify(self, claim: "Claim", ctx: "VerificationContext") -> "VerificationOutcome": ...

class PipelineStage(Protocol):
    name: "StageName"  # S1..S9
    async def run(self, run_id: UUID) -> "StageResult": ...  # idempotent theo từng mục

# --- Bổ sung theo 07a §4.11 ---

class ContextBuilder(Protocol[InT]):
    task: str
    def build(self, inp: InT) -> "PromptParts": ...  # stable_prefix, variable, id_map (ID ngắn ↔ UUID)

class Tool(Protocol):
    name: str
    description: str                 # nạp từ prompts/tools/<catalog>/<version>.yaml
    input_model: type[BaseModel]     # strict, additionalProperties=false
    async def __call__(self, args: BaseModel) -> "ToolResult": ...  # ToolResult | ToolError(code, message, fix)

class AgentRunner(Protocol):
    async def run(self, *, agent: str, version: str, parts: "PromptParts",
                  tools: "Sequence[Tool]", submit_model: type[T],
                  budget: "AgentBudget", cache_namespace: str) -> "AgentOutcome[T]": ...

class ReferenceResolver(Protocol):   # CascadeResolver, AgentResolver (A1)
    async def resolve(self, entry: "ReferenceEntry", prior: "CascadeResult | None") -> "ResolutionResult": ...
```

`AgentBudget` gồm `max_steps`, `max_context_tokens`, `max_total_input_tokens`, `max_wall_seconds`, `max_submit_attempts` (`07a` §4.5.1). Đây là bản phác thảo; tên kiểu cụ thể chốt khi viết mã.

`ConnectorInterface` của v1.0 §9.2 được tách thành ba protocol: `MetadataConnector`, `AccessResolver`, `FullTextFetcher`. Lý do: không nguồn nào hỗ trợ đủ cả ba chức năng.

## 5. Implementation Steps

- [ ] Viết các schema Pydantic từ §4.4 trong `domain/` (T-104, T-107).
- [ ] Sinh tài liệu OpenAPI tự động từ FastAPI; giữ tài liệu này ở mức tổng quan.
- [ ] Viết API test cho mọi endpoint P0, kể cả kiểm tra quyền truy cập (`13`).

## 6. Expected Outputs

Danh sách endpoint, data contract và interface nội bộ được chốt.

## 7. Dependencies

`04` (FR), `07` (module, giai đoạn), `08` (enum), `10` (xác thực, giới hạn).

## 8. Risks and Considerations

| Rủi ro | Giảm thiểu |
|---|---|
| Lộ dữ liệu giữa người dùng (IDOR) | Mọi truy vấn lọc theo `user_id`; trả 404 cho tài nguyên của người khác; test riêng |
| Contract thay đổi làm hỏng dữ liệu đánh giá cũ | Đánh phiên bản schema `Verdict`; lưu phiên bản cùng kết quả |
| Polling gây tải | Khoảng polling hợp lý; đủ cho quy mô pilot |

## 9. Acceptance Criteria

- [ ] Mọi FR P0 có endpoint hoặc luồng giao diện tương ứng.
- [ ] Mọi schema khớp enum ở `08`.
- [ ] Interface nội bộ đủ để thay connector, verifier và LLM client mà không sửa module khác.

## 10. References

- CiteProof AI Project Documentation v1.0: §9.2, §13.
- `07-technical-architecture.md` §4.4–§4.7.
