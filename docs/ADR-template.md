# ADR Template

Mỗi quyết định kiến trúc quan trọng được ghi thành một file riêng trong `docs/decisions/`, đặt tên `ADR-<số>-<tên-ngắn>.md`, ví dụ `ADR-002-pipeline-orchestration.md`. Không sửa nội dung một ADR đã Accepted; nếu đổi quyết định, tạo ADR mới và đánh dấu ADR cũ là Superseded.

Trạng thái: `Proposed` → `Accepted` / `Rejected` → (sau này) `Superseded by ADR-xxx`.

---

```markdown
# ADR-xxx: <Tiêu đề quyết định>

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Proposed / Accepted / Rejected / Superseded by ADR-yyy |
| Ngày | YYYY-MM-DD |
| Liên quan | <FR/NFR, D-xx, SPK-xx, tài liệu> |

## Context
<Bối cảnh: điều gì dẫn đến cần quyết định; ràng buộc liên quan>

## Problem
<Câu hỏi cần trả lời, viết thành một câu>

## Alternatives
| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A | | |
| B | | |

## Decision
<Phương án được chọn>

## Rationale
<Vì sao chọn; dựa trên bằng chứng nào (spike, số đo, tài liệu)>

## Consequences
- Tích cực: ...
- Tiêu cực / chấp nhận đánh đổi: ...
- Việc cần làm tiếp: <tài liệu, task bị ảnh hưởng>
```

---

## ADR đề xuất từ `07-technical-architecture.md` §4.11

| ADR | Chủ đề | Phụ thuộc |
|---|---|---|
| ADR-001 | Giữ Python, FastAPI, Pydantic v2, PostgreSQL + pgvector, SQLAlchemy, Alembic | — |
| ADR-002 | Điều phối: pipeline tự viết hay LangGraph | — |
| ADR-003 | Hàng đợi tác vụ: PostgreSQL hay Redis | — |
| ADR-004 | Parse PDF: GROBID | SPK-01 |
| ADR-005 | Giao diện pilot: server-rendered hay Next.js | — |
| ADR-006 | Lớp truy cập LLM | Q-01, Q-03 |
| ADR-007 | Embedding và chiến lược xuyên ngôn ngữ | SPK-04 |
| ADR-008 | Tái sử dụng hay tự xây kiểm tra tài liệu tham khảo | T-002 |
