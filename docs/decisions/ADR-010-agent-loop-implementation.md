# ADR-010: Hiện thực vòng lặp agent

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted (25/11/2026), hiện thực ở T-406 |
| Ngày | 2026-11-25 (đề xuất 2026-11-23) |
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

Chọn phương án A: `llm/agent_runner.py` (`AgentRunner`) và `llm/tools.py` (`Tool`, `ToolError`, `ToolResult`, `SubmitTool`, `load_tool_catalog`), viết cạnh `LLMClient` và dùng chung `CacheStore`, `CallLogger`, bảng giá. Provider có thêm `complete_turn(TurnRequest) -> TurnResponse`; cú pháp tool use, `strict`, `output_config.effort`, `cache_control` và luồng `get_final_message()` đã đối chiếu với SDK `anthropic` 2.13 (context7 và mã nguồn gói).

Hiện thực (spec §9):

- Lượt agent luôn đi đường `messages.stream` thường, không dùng fallback phía server (QĐ-36); công cụ gửi `strict: true`, không gửi `tool_choice`, `thinking` hay tham số sampling. Khối nội dung trả về (kể cả thinking có chữ ký) được gửi lại nguyên vẹn; hội thoại chỉ nối thêm.
- `max_steps` đếm lời gọi công cụ (kể cả công cụ lạ, đối số sai), không tính submit. Dùng hết thì lượt kết quả thêm "You have one call left: submit now with what you have." và lượt kế là lượt cuối, chỉ nhận submit; lời gọi song song vượt phần còn lại nhận lỗi `STEP_BUDGET_EXHAUSTED`.
- Khóa cache từng lượt: `agent_step_cache_key` trên `[system, tools, *messages]`; entry giữ cả số token nên phát lại cho đúng quyết định ngân sách cũ.
- `agent_runs` ghi ngay khi bắt đầu với `ERROR`, cập nhật khi kết thúc; mỗi lượt một dòng `llm_calls` (`agent_run_id`, `step_index`) commit ngay để cổng chi phí S8 thấy.

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
