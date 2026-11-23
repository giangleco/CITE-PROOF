# ADR-010: Hiện thực vòng lặp agent

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Proposed. Chốt khi bắt đầu T-406 |
| Ngày | 2026-11-23 |
| Liên quan | ADR-002, ADR-006, ADR-009, NFR-06, `07a` §4.5.1 |

## Context

- A1 và A2 cần một vòng lặp: gọi LLM kèm công cụ, chạy công cụ, lặp lại cho đến khi agent nộp kết quả hoặc hết ngân sách.
- Cache, retry và log chi phí (`llm_calls`) đã tập trung ở `LLMClient` (ADR-006).
- Một số model mới không cho ép gọi công cụ (`tool_choice` any/tool) và đã bỏ tham số nhiệt độ.
- Tính tái lập phải dựa vào cache theo từng bước.

## Problem

Viết vòng lặp agent bằng gì để giữ được cache theo bước, ngân sách cứng và log tập trung?

## Alternatives

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A. Tự viết trên `LLMClient`, khoảng 200 dòng | Dùng chung cache, namespace và log; ngân sách cứng do runner đếm; không thêm phụ thuộc | Phải tự viết phần xử lý lời gọi công cụ song song và lời nhắc khi agent chưa nộp kết quả |
| B. PydanticAI | Tool có kiểu; có cơ chế thử lại khi validate lỗi; hỗ trợ nhiều nhà cung cấp | Lớp model riêng, khó móc vào cache và `llm_calls` của `LLMClient`; thêm một lớp trừu tượng |
| C. LangGraph | Có checkpointer và đồ thị trạng thái | Quá nặng cho vòng lặp ≤8 bước; mâu thuẫn tinh thần ADR-002 |
| D. Tool runner trong SDK của nhà cung cấp | Ít mã | Khóa chặt vào một nhà cung cấp; cache và log nằm ngoài `LLMClient` |

## Decision

Đề xuất phương án A. Trước khi viết mã, tra cú pháp mới nhất của SDK nhà cung cấp đã chọn ở ADR-006 (context7 hoặc tài liệu chính thức) cho phần tool use và structured output.

## Rationale

- Cache theo bước (khóa là hash của hội thoại) và ngân sách cứng là hai yêu cầu cốt lõi (`07a` §4.5.1, §4.7.2).
- Phương án A đáp ứng cả hai mà không cần lớp chuyển đổi.

## Consequences

- **Tích cực:**
  - Một chỗ duy nhất cho cache, log và chi phí.
  - Phát lại đúng quỹ đạo cũ.
- **Đánh đổi:**
  - Tự bảo trì phần xử lý tool use khi API nhà cung cấp thay đổi; giảm thiểu bằng test ghi/phát lại.
- **Việc tiếp theo:** T-406.
