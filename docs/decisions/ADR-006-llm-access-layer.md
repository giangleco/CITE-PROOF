# ADR-006: Lớp truy cập LLM — Anthropic SDK sau `LLMClient`

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted (24/11/2026); Superseded in part by ADR-011 (27/11/2026: thêm provider tương thích OpenAI, chọn provider theo cấu hình). Q-01 (ngân sách) và Q-03 (chính sách dữ liệu nhà cung cấp) vẫn mở |
| Ngày | 2026-11-24 |
| Liên quan | FR-45, FR-63, FR-81, NFR-05, NFR-06, NFR-08, SR-08, `07` §4.2, §4.6, `07a` §4.4, §4.5, §4.7.2, `12` §4.8, ADR-009, ADR-010, spec `superpowers/specs/2026-11-24-core-pipeline-design.md` (QĐ-01, QĐ-36, QĐ-37) |

## Context

- M1 đã có `LLMClient`: cache theo namespace (`llm_cache`), validate Pydantic, thử lại một lần kèm `SCHEMA_INVALID`, ghi `llm_calls`, chế độ phát lại; bên dưới là protocol `LLMProvider` và một provider kịch bản cho test.
- Cần năm worker (W1–W5) với đầu ra có cấu trúc và hai agent (A1, A2) có công cụ chỉ đọc.
- API hiện hành của các model mới từ chối `temperature`/`top_p`/`top_k` và không cho ép `tool_choice`; tái lập phải dựa vào cache và model ID cố định (`07a` §4.7.2).
- Hệ thống phải chạy được khi chưa có khóa API (chưa chốt ngân sách).

## Problem

Gọi LLM bằng gì, sau lớp nào, để giữ một chỗ cho cache, log chi phí và phát lại, đồng thời dùng đúng các tính năng structured output và tool use của nhà cung cấp?

## Alternatives

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A. SDK `anthropic` chính thức, cài thành `AnthropicProvider` sau `LLMClient` | Dùng được structured output, strict tool use, prompt caching, fallback khi model từ chối; cache và log giữ nguyên ở `LLMClient` | Gắn với một nhà cung cấp ở tầng provider |
| B. LiteLLM hoặc thư viện đa nhà cung cấp | Đổi nhà cung cấp bằng cấu hình | Thêm lớp trừu tượng; tính năng mới (structured output, refusal fallback, thinking) đến chậm hoặc bị làm phẳng |
| C. Gọi SDK trực tiếp trong từng module | Ít mã | Phá quy tắc `12` §4.8 mục 1; cache và log rải rác |
| D. Hỗ trợ nhiều nhà cung cấp ngay | Linh hoạt | YAGNI cho một người làm; nhân đôi test |

## Decision

Chọn A:

- `AnthropicProvider` dùng `AsyncAnthropic` (SDK `anthropic>=2.13,<3`), khóa từ `CITEPROOF_ANTHROPIC_API_KEY`. Không có khóa thì `make_llm_client` trả `None`; các bước LLM giảm cấp tất định và lần chạy có cảnh báo `LLM_NOT_CONFIGURED`.
- Worker dùng `output_config.format` (JSON schema, đã bỏ các ràng buộc API chưa hỗ trợ; Pydantic vẫn kiểm đủ). Agent dùng tool `strict: true`, `tool_choice` mặc định (`auto`), nội dung phản hồi gửi lại nguyên vẹn, hội thoại chỉ nối thêm.
- Model theo tác vụ, cấu hình được, đóng băng vào `verification_runs.config` khi tạo lần chạy: W1–W4 `claude-haiku-5-5`, W5/A1/A2 `claude-sonnet-5-5`; `effort` W1–W4 `low`, W5/A1/A2 `high`. Không gửi tham số sampling, không ép công cụ, không đặt `thinking` (adaptive là mặc định).
- Phần system prompt cố định có `cache_control` để tận dụng prompt caching.
- Worker gọi Sonnet bật fallback phía server khi model từ chối (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`), tắt được bằng `CITEPROOF_ANTHROPIC_REFUSAL_FALLBACK=false`; model thực sự trả lời được ghi vào `llm_calls.model` và `claim_verifications.model`. Agent không dùng fallback; từ chối thành trạng thái `ERROR` và kết quả dự phòng của `07a` §4.3.5.
- Test chỉ dùng provider kịch bản và đối tượng SDK giả, không gọi mạng; một test chạy thật duy nhất bật bằng `CITEPROOF_LIVE_LLM=1`.

## Rationale

- Cache, log và phát lại là yêu cầu cốt lõi (NFR-05, NFR-06, NFR-08) và đã nằm ở `LLMClient`; chỉ cần thêm một provider.
- SDK chính thức có sẵn các tính năng thiết kế cần; lớp đa nhà cung cấp không đem lại gì khi chỉ dùng một nhà cung cấp.
- Haiku cho tác vụ cục bộ, Sonnet cho phán quyết theo cân nhắc chi phí ở `07a` §4.8.5.

## Consequences

- **Tích cực:** một chỗ cho chi phí; đổi model không cần sửa mã; chạy được khi chưa có khóa.
- **Đánh đổi:** đổi nhà cung cấp cần một provider mới; fallback phía server có thể làm một phần kết quả đến từ model khác (đã ghi lại); tham số `effort` thay đổi khóa cache.
- **Việc tiếp theo:** trả lời Q-01 (`run_max_cost_usd`) và Q-03 (chính sách lưu dữ liệu, đặt `CITEPROOF_LLM_PROVIDER_NAME`, `CITEPROOF_LLM_PROVIDER_POLICY_URL`); cập nhật `07` §4.2, §4.11; ADR-010 chốt khi làm `AgentRunner`.
