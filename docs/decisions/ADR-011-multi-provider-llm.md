# ADR-011: LLM đa nhà cung cấp — thêm provider tương thích OpenAI

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted (27/11/2026) |
| Ngày | 2026-11-27 |
| Liên quan | ADR-006 (Superseded in part), ADR-010, Q-01, Q-03, NFR-05, NFR-06, spec `superpowers/specs/2026-11-27-multi-provider-and-completion-design.md` (§4, §5.1a, §5.2, §5.5, §5.6) |

## Context

- ADR-006 chọn SDK `anthropic` sau `LLMClient` vì khi đó chỉ dùng một nhà cung cấp. Hãng LLM thật sự dùng cho sản phẩm chưa được chọn: phụ thuộc nhà đầu tư (Q-01, Q-03 vẫn mở).
- Máy phát triển chưa có khóa Anthropic nên chưa kiểm được luồng LLM và agent bằng lời gọi thật.
- Protocol `LLMProvider` đã tách `LLMClient`/`AgentRunner` khỏi SDK, nên thêm provider không đụng cache, log chi phí hay phát lại.
- Quy tắc của ADR-006 giữ nguyên: không gửi tham số sampling, thông điệp lỗi không chứa prompt.

## Problem

Làm sao để CiteProof chạy được với hãng LLM mà nhà đầu tư sẽ chọn (và với model local để kiểm luồng chạy ngay), mà không phá cache, tái lập và quy tắc quyền riêng tư hiện có?

## Alternatives

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A. Giữ Anthropic, chỉ thêm chỗ cắm và contract test | Ít việc nhất | Hôm nay vẫn một provider; không kiểm live được khi chưa có khóa |
| B. A + provider tương thích OpenAI | Một adapter phủ hầu hết các hãng và model local; kiểm live được ngay bằng model local | Tốn công chuyển đổi định dạng agent; model local nhỏ làm structured output và tool call kém, chỉ dùng để kiểm luồng chạy |
| C. LiteLLM | Phủ rộng nhất | Làm phẳng tính năng riêng, thêm phụ thuộc lớn; ADR-006 đã loại |

## Decision

Chọn B (chủ dự án chọn ngày 27/11/2026). `make_provider(settings)` trong `llm/factory.py` chọn `AnthropicProvider` hoặc `OpenAICompatibleProvider` theo `CITEPROOF_LLM_PROVIDER`, và trả `None` khi chưa cấu hình LLM. `make_llm_client` và `make_agent_runner` dùng nó; tham số `provider=` (test, bộ đánh giá) giữ nguyên.

## Rationale

- Chat Completions là phần mọi server tương thích đều có; một adapter phủ OpenAI, OpenRouter, Ollama, vLLM.
- Contract test tham số hóa theo provider (`tests/llm/contract/`), chạy trên SDK giả, giữ hai provider cùng hành vi: JSON worker, lý do dừng, khứ hồi tool call, ánh xạ lỗi không lộ prompt.
- Test live dùng chung (`tests/llm/test_llm_live.py`) chạy với provider đang cấu hình, nên kiểm được bằng server local khi chưa có khóa hãng.

## Consequences

- Tích cực: không bị khóa vào một hãng; kiểm live được bằng model local; `LLMClient`, cache và phát lại không đổi.
- Tiêu cực / chấp nhận đánh đổi:
  - Không có fallback phía server khi model từ chối và không có prompt caching ngoài Anthropic; chi phí và độ trễ phụ thuộc hãng.
  - Structured output dùng `strict: false` vì chế độ strict của OpenAI bắt mọi trường phải `required`, trái schema Pydantic có trường mặc định; Pydantic ở `LLMClient` vẫn kiểm đủ và thử lại một lần.
  - Định danh provider theo host (`openai_compatible:<host>[:<port>]`, Anthropic giữ `anthropic`): đổi host là đổi provider, không dùng chung cache với provider cũ, chặn tiếp tục run cũ (`LLM_PROVIDER_CHANGED`) và bắt người dùng đồng ý lại.
  - Tên model mặc định chỉ dành cho Anthropic; `openai_compatible` phải đặt `CITEPROOF_LLM_MODELS` và `CITEPROOF_LLM_PRICES`.
- Việc cần làm tiếp: đóng băng provider vào `RunConfig`, đồng ý lại khi đổi provider, bộ đánh giá dùng `make_provider` (các task sau của kế hoạch).
