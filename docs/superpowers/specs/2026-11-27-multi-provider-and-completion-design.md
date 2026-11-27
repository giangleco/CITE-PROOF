# Thiết kế: LLM đa nhà cung cấp và hoàn thiện sau audit

| Thuộc tính | Giá trị |
|---|---|
| Ngày | 27/11/2026 |
| Trạng thái | Chờ duyệt |
| Liên quan | `docs/completion-audit.md`, ADR-006, ADR-010, `07a` §4.7.2, spec `2026-11-24-core-pipeline-design.md` |

## 1. Mục đích và lý do

Chủ dự án **chưa có API key và chưa biết sẽ dùng nhà cung cấp LLM nào**; việc đó phụ thuộc vào nhà đầu tư. Yêu cầu là: khi có vốn, chỉ cần **thêm cấu hình và key là chạy được**, không phải sửa mã lớn. Đồng thời phải làm nốt các đợt 1–4 trong `docs/completion-audit.md`.

Hệ quả của việc chưa có key: kiểm tra live (đợt 4) phải chạy được với **bất kỳ provider nào đang cấu hình**, kể cả một model chạy trên máy không cần key.

## 2. Mục tiêu, không làm, phản mục tiêu

**Mục tiêu**
- G1. Chọn nhà cung cấp bằng cấu hình: `anthropic` (đã có) hoặc `openai_compatible` (mới). Provider thứ hai phủ OpenAI, Gemini (endpoint tương thích OpenAI), DeepSeek, OpenRouter, Ollama/vLLM.
- G2. Cả worker (W1–W5) lẫn agent (A1, A2) chạy được trên cả hai provider.
- G3. Một bộ **contract test** chung mà mọi provider phải qua; provider tương lai chỉ cần qua bộ này.
- G4. Không thể vô tình chạy model không có giá (trần chi phí phải luôn có hiệu lực).
- G5. Làm xong đợt 1–4 của audit.

**Không làm**
- Chọn provider khác nhau cho từng tác vụ (W1 một hãng, W5 hãng khác). Cả hệ thống dùng một provider; model theo tác vụ vẫn cấu hình được như hiện nay.
- Tự động chuyển sang provider dự phòng khi một bên lỗi.
- LiteLLM hoặc thư viện đa nhà cung cấp (ADR-006 đã loại).
- Cài Ollama hoặc model local vào máy chủ (chỉ hướng dẫn).
- Backend S3 (T-202), việc nghiên cứu/dữ liệu/pilot ở audit §3.3.

**Phản mục tiêu** (chạy được nhưng vẫn là thất bại)
- Đổi provider làm hỏng hành vi Anthropic hiện có: structured output, refusal fallback, prompt caching, streaming, 1649 test hiện tại.
- Lần chạy dùng model không có giá → chi phí ghi 0 → `max_cost_usd` không bao giờ chặn.
- Cache LLM của provider này bị dùng cho provider khác.
- Một lần chạy đang dở, khi tiếp tục, lặng lẽ chuyển sang provider khác.
- Prompt hoặc đầu ra lọt vào thông điệp lỗi/log (quy tắc hiện có của `AnthropicProvider._describe`).

## 3. Ràng buộc từ mã hiện tại

- `LLMProvider` protocol (`llm/providers/base.py`): `name`, `complete(ProviderRequest)`, `complete_turn(TurnRequest)`, tùy chọn `aclose()`.
- Cache key worker và agent đã gồm `provider.name` (`client.py:93`, `agent_runner.py:440`), nên cache tự tách theo provider.
- **Định dạng hội thoại agent là dạng khối của Anthropic**: `AgentRunner` lưu và gửi lại nguyên `response.content`, tìm khối `type == "tool_use"`, trả `{"type":"tool_result","tool_use_id","content"}` và khối `{"type":"text"}`. Đây là **định dạng chuẩn nội bộ**; provider khác phải tự chuyển đổi hai chiều.
- `LLMClient` coi `stop_reason` thuộc `{"max_tokens","refusal"}` là đầu ra hỏng (`client.py:32`). Provider mới phải đổi lý do dừng về các giá trị này.
- `estimate_cost` trả 0 cho model không có trong `llm_prices` (`calls.py:79`).
- `RunConfig` đóng băng `models`, `effort`, `max_tokens` khi tạo run (`pipeline/config.py`).
- Không có key → `make_llm_client` trả `None` → các bước LLM hạ cấp (`LLM_NOT_CONFIGURED`). Hành vi này giữ nguyên.
- Quy tắc của người dùng: tra context7 cú pháp SDK mới nhất trước khi viết mã OpenAI/Anthropic SDK.

## 4. Các phương án (đã chọn B)

| Phương án | Đánh đổi |
|---|---|
| A. Giữ Anthropic, chỉ thêm chỗ cắm và contract test | Ít việc; nhưng hôm nay vẫn một provider và không kiểm live được khi chưa có key |
| **B. A + provider tương thích OpenAI (chọn)** | Một adapter phủ hầu hết các hãng và model local; kiểm live được ngay bằng model local. Tốn công chuyển đổi định dạng agent; model local nhỏ làm structured output/tool call kém hơn, chỉ dùng để kiểm luồng chạy |
| C. LiteLLM | Phủ rộng nhất nhưng làm phẳng tính năng riêng và thêm phụ thuộc lớn; ADR-006 đã loại |

Chủ dự án chọn B ngày 27/11/2026.

## 5. Thiết kế provider đa nhà cung cấp

### 5.1 Cấu hình (`core/settings.py`, `.env.example`, `.env.production.example`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `CITEPROOF_LLM_PROVIDER` | `anthropic` | `anthropic` hoặc `openai_compatible` |
| `CITEPROOF_OPENAI_BASE_URL` | trống | Bắt buộc với `openai_compatible` (ví dụ `https://api.openai.com/v1`, `http://localhost:11434/v1`) |
| `CITEPROOF_OPENAI_API_KEY` | trống | Tùy chọn (server local không cần) |
| `CITEPROOF_OPENAI_TIMEOUT_SECONDS` | `600` | Gọi không luồng nên timeout dài (xem 5.3) |
| `CITEPROOF_OPENAI_MAX_RETRIES` | `2` | Như Anthropic |
| `CITEPROOF_OPENAI_STRUCTURED_OUTPUT` | `json_schema` | `json_schema` \| `json_object` \| `none`; server không hỗ trợ JSON schema thì hạ xuống |
| `CITEPROOF_OPENAI_SEND_REASONING_EFFORT` | `false` | Bật thì gửi `effort` thành `reasoning_effort` (model suy luận của OpenAI); mặc định không gửi vì nhiều server từ chối tham số lạ |

Giữ nguyên `CITEPROOF_LLM_MODELS`, `CITEPROOF_LLM_PRICES`, `CITEPROOF_LLM_EFFORT`, `CITEPROOF_LLM_MAX_TOKENS` (JSON), `CITEPROOF_LLM_PROVIDER_NAME`, `CITEPROOF_LLM_PROVIDER_POLICY_URL` (màn hình đồng ý).

**"Đã cấu hình LLM"** nghĩa là: `anthropic` có key, hoặc `openai_compatible` có `base_url`. Ngược lại vẫn trả `None` như hiện nay.

**Kiểm tra khi khởi động** (validator của `Settings`, chặn app/worker chạy với cấu hình sai, thông điệp tiếng Việt):
1. `openai_compatible` mà `llm_models` còn đúng bảng mặc định model Claude → lỗi "đặt CITEPROOF_LLM_MODELS cho nhà cung cấp mới".
2. Khi LLM đã cấu hình, mọi model trong `llm_models` phải có mặt trong `llm_prices` → nếu thiếu thì lỗi, liệt kê model thiếu giá. Model local khai giá `[0, 0]` một cách tường minh. (Đáp ứng G4.)

### 5.1a Định danh provider (lựa chọn của người viết thiết kế)

Mỗi provider có **định danh** (`provider.name`) dùng cho cache key, cho `RunConfig.provider` (5.4) và cho bản ghi đồng ý (5.4a):
- Anthropic: `anthropic` (giữ nguyên, nên cache key hiện có không đổi).
- Tương thích OpenAI: `openai_compatible:<host>[:<port>]` lấy từ `CITEPROOF_OPENAI_BASE_URL`, chữ thường, bỏ scheme và path (ví dụ `openai_compatible:api.openai.com`, `openai_compatible:localhost:11434`).

Lý do: đổi từ Ollama sang hãng thật, hay từ OpenRouter sang OpenAI, là đổi nơi dữ liệu được gửi tới và đổi model thật, nên phải được coi là đổi provider (không dùng chung cache, chặn tiếp tục run cũ, đòi đồng ý lại).

`make_provider_identity(settings) -> str | None` (None khi chưa cấu hình LLM) là nguồn duy nhất; provider, `RunConfig` và màn hình đồng ý đều lấy từ đây.

### 5.2 Factory (`llm/factory.py`)

`make_provider(settings) -> LLMProvider | None` chọn theo `llm_provider`; `make_anthropic_provider` giữ nguyên. `make_llm_client`/`make_agent_runner` gọi `make_provider` thay vì `make_anthropic_provider`. Tham số `provider=` (dùng cho test/đánh giá) giữ nguyên.

### 5.3 `OpenAICompatibleProvider` (`llm/providers/openai_compatible.py`)

Dùng SDK chính thức `openai` (`AsyncOpenAI(base_url=..., api_key=... or "unused")`), Chat Completions API vì đó là phần mọi server tương thích đều có. `name` theo 5.1a (`openai_compatible:<host>`).

**Worker (`complete`)**
- `system` → tin nhắn `{"role":"system"}`; `messages` giữ nguyên (chuỗi).
- Structured output theo `openai_structured_output`:
  - `json_schema`: `response_format={"type":"json_schema","json_schema":{"name":<task>,"schema":strict_output_schema(...),"strict":false}}`. Dùng `strict:false` vì chế độ strict của OpenAI bắt mọi trường phải `required`, trái với schema Pydantic có trường mặc định. Pydantic ở `LLMClient` vẫn kiểm đủ và thử lại một lần như hiện nay.
  - `json_object`: `response_format={"type":"json_object"}` và nối JSON schema vào cuối system prompt.
  - `none`: chỉ nối schema vào system prompt.
- Không gửi tham số sampling (giữ quy tắc ADR-006). `effort` → `reasoning_effort` chỉ khi bật cờ.
- `max_tokens` → `max_completion_tokens`.
- Gọi không luồng với timeout dài (5.1). Đánh đổi: đơn giản hơn nhiều so với ghép luồng tool call; W5 dài vẫn nằm trong 600 s.

**Agent (`complete_turn`)**: chuyển đổi hai chiều giữa định dạng khối nội bộ và Chat Completions:

| Nội bộ (Anthropic) | Chat Completions |
|---|---|
| user, `content` là chuỗi | `{"role":"user","content":str}` |
| user, khối `text` | ghép thành `{"role":"user","content":...}` |
| user, khối `tool_result` | mỗi khối → `{"role":"tool","tool_call_id":tool_use_id,"content":...}` đặt **trước** tin user chứa text cùng lượt |
| assistant, khối `text` + `tool_use` | `{"role":"assistant","content":text or None,"tool_calls":[{"id","type":"function","function":{"name","arguments":json.dumps(input)}}]}` |
| khối `thinking`/khối lạ trong lịch sử | bỏ qua |
| `ToolSpec` | `{"type":"function","function":{"name","description","parameters":strict_output_schema(...)}}` (không `strict`) |

Chiều về: `message.content` → khối `text` (nếu có), mỗi `tool_calls[i]` → `{"type":"tool_use","id","name","input":json.loads(arguments)}`. `arguments` không phải JSON hợp lệ → `input = {}` để công cụ trả lỗi kiểm tra đầu vào cho agent như hiện nay (lựa chọn của người viết thiết kế; builder có thể đổi nếu `AgentRunner` có đường tốt hơn).

**Lý do dừng** (`finish_reason` → `stop_reason`): `stop`→`end_turn`, `tool_calls`→`tool_use`, `length`→`max_tokens`, `content_filter`→`refusal`, giá trị khác giữ nguyên. Tin nhắn có trường `refusal` khác rỗng → `refusal`.

**Token**: `usage.prompt_tokens`/`completion_tokens`; server không trả `usage` → 0 và log cảnh báo một lần (không chứa nội dung). `model` lấy từ phản hồi, trống thì dùng model đã yêu cầu.

**Lỗi**: như `_mapped` của Anthropic: `RateLimitError`, `APIConnectionError`/timeout, `APIStatusError` ≥ 500 → `RetryableError`; 4xx khác → `ExternalServiceError`; thông điệp chỉ `openai_compatible <TênLỗi> status=<mã>`.

**Không có** ở provider này: refusal fallback phía server, `cache_control` (OpenAI tự cache prompt). `anthropic_refusal_fallback` bị bỏ qua.

### 5.4 Đóng băng provider vào lần chạy

`RunConfig` thêm trường `provider: str | None`, đặt bằng định danh (5.1a) khi tạo run.
- Run tạo khi **chưa cấu hình LLM** lưu `None` (không gắn provider): tiếp tục được với provider nào cũng được (lựa chọn của người viết thiết kế; các run này chưa gọi LLM nào).
- Run cũ không có trường này: nạp là `anthropic` nếu `llm_calls` của run có dòng provider `anthropic`, ngược lại `None`. Builder có thể đơn giản hóa thành "nạp là `None`" nếu kiểm tra thấy chưa có run nào gọi LLM thật (máy hiện chưa có key).
- Khi stage cần LLM mà provider của worker khác provider đã đóng băng (và giá trị đó khác `None`) → lỗi không thử lại `LLM_PROVIDER_CHANGED`.

**Báo cáo cũ sau khi đổi provider (chủ dự án chọn: chặn).** Lần chạy con (`services/reverify.py` chép cấu hình run cha) kế thừa provider đã đóng băng. Vì vậy, khi provider hiện tại khác provider của run cha, các hành động "Kiểm chứng lại", "Tra cứu lại", "Tải nguồn còn thiếu" bị **chặn ngay ở service trước khi tạo run con**, với thông điệp tiếng Việt trong `web/labels.py` và `.stitch/DESIGN.md`: "Nhà cung cấp mô hình đã đổi; hãy tạo lần kiểm tra mới cho tài liệu này." Không trộn kết quả của hai provider trong một báo cáo. Trang báo cáo vẫn xem và xuất được như cũ. Nếu đơn giản, ẩn các nút đó và hiện thông điệp thay vì để người dùng bấm rồi mới báo lỗi (builder quyết).

### 5.4a Đồng ý lại khi đổi provider (chủ dự án chọn: bắt buộc, tự động)

- Migration thêm cột `users.consent_llm_provider` (text, nullable).
- Khi người dùng đồng ý: lưu định danh provider hiện tại (5.1a) cùng `consent_llm_at`.
- `needs_consent` (`services/auth.py:219`) thêm điều kiện: LLM đã cấu hình và `consent_llm_provider` khác định danh hiện tại → phải đồng ý lại. Điều kiện cũ theo `consent_text_updated_at` giữ nguyên.
- Người dùng đã đồng ý trước migration (`consent_llm_provider` NULL): coi như đã đồng ý với provider đang cấu hình lúc migration chạy **chỉ khi** provider đó là `anthropic` hoặc chưa cấu hình; ngược lại phải đồng ý lại. Cách đơn giản nhất: không backfill, và coi NULL là "khớp" khi định danh hiện tại là `anthropic` hoặc None. Builder có thể chọn cách tương đương.
- Màn hình đồng ý hiện tên hãng (`CITEPROOF_LLM_PROVIDER_NAME`) như hiện nay. Validator 5.1 thêm: `openai_compatible` mà chưa đặt `CITEPROOF_LLM_PROVIDER_NAME` → cảnh báo khi khởi động (không chặn, vì Q-03 vẫn mở).
- Người dùng eval (`evaluation/importer.py`) được tạo với `consent_llm_provider` bằng định danh hiện tại.

### 5.5 Contract test (`tests/llm/contract/`)

Một bộ test tham số hóa theo provider, chạy với SDK giả (httpx `MockTransport` hoặc client giả, không gọi mạng):
- worker trả JSON → `ProviderResponse` đúng text, token, model;
- đổi lý do dừng (`max_tokens`, `refusal`, kết thúc thường);
- lượt agent: tool_use ra → tool_result vào → lượt tiếp theo nhận đúng lịch sử (khứ hồi qua `AgentRunner` với một công cụ giả);
- đổi lỗi: 429/5xx/lỗi kết nối → `RetryableError`, 4xx → `ExternalServiceError`, thông điệp không chứa prompt.

Test riêng của `AnthropicProvider` hiện có giữ nguyên; contract test chạy thêm cho Anthropic.

### 5.6 Test live dùng chung

`tests/llm/test_anthropic_live.py` đổi thành `test_llm_live.py`: chạy khi `CITEPROOF_LIVE_LLM=1`, dùng **provider đang cấu hình** qua `make_provider`; một worker structured output nhỏ và một lượt agent một công cụ. Không cần key nếu trỏ tới server local.

### 5.6a Bộ đánh giá (lựa chọn của người viết thiết kế: hỗ trợ mọi provider)

Nếu không, khi nhà đầu tư chọn hãng khác thì không đo lại được kết quả.
- `evaluation/runner.py::_provider_for`: chế độ `read_write`/`fresh` dùng `make_provider`; lỗi `LLM_NOT_CONFIGURED` nói chung "chưa cấu hình LLM" thay vì nhắc riêng khóa Anthropic.
- `ReplayOnlyProvider.name` lấy định danh hiện tại (5.1a), mặc định `anthropic` khi chưa cấu hình, để phát lại đúng cache của provider đã ghi. Thêm tùy chọn CLI `--provider-identity` để phát lại cache của một provider khác mà không cần cấu hình nó.
- `report.md` của đánh giá ghi định danh provider.

### 5.7 Tài liệu

- **ADR-011** "LLM đa nhà cung cấp qua provider tương thích OpenAI", thay một phần ADR-006 (phương án D); ADR-006 thêm dòng "Superseded in part by ADR-011".
- `docs/14` và README: bảng cách cấu hình cho Anthropic, OpenAI, Gemini, OpenRouter, Ollama (ví dụ `CITEPROOF_LLM_MODELS`/`CITEPROOF_LLM_PRICES`); mục "Thêm một provider mới": cài `LLMProvider`, qua contract test, đăng ký ở `make_provider`.
- Phụ thuộc mới: `openai` (ghim khoảng phiên bản lớn như `anthropic`).

## 6. Đợt 1–4 của audit

**Đợt 1 — env và tài liệu** (audit A-1…A-4)
- `docker-compose.yml`: `migrate`/`app`/`worker` thêm `env_file: [{path: .env, required: false}]`; giữ `CITEPROOF_DATABASE_URL`, `CITEPROOF_STORAGE_DIR`, `CITEPROOF_GROBID_URL` trong `environment` để ghi đè giá trị `localhost` của `.env`. Thêm test trong `tests/deploy/`. Nếu `OPENAI_BASE_URL` trỏ `localhost` thì trong container phải là `host.docker.internal` (thêm `extra_hosts: host-gateway` cho worker) — ghi chú trong README.
- README: bỏ câu "các bước xử lý thật chưa có"; thêm cách bật LLM (cả hai provider), GROBID, `make eval`.
- `docs/project-status.md`: cập nhật phase/milestone theo `task-tracking.md`.
- `docs/task-tracking.md`: "nhánh `feat/core-pipeline`" → "trên `main`". Không xóa nhánh (chủ dự án chọn giữ).

**Đợt 2 — việc hoãn P1** (audit §3.5)
- Bước đầu: phân loại toàn bộ dòng `minor (deferred)` trong hai file handoff thành *sửa* / *chấp nhận, ghi giới hạn* / *đã xử lý*, ghi thành bảng ở cuối `docs/completion-audit.md`.
- Phạm vi sửa (lựa chọn của người viết thiết kế): **chỉ 9 dòng P1 ở audit §3.5**. Mục khác được phân loại là *sửa* thì ghi vào bảng với nhãn "để sau", không làm trong kế hoạch này.
- Sửa các mục P1 đó, mỗi mục theo TDD (test đỏ trước) và một commit. Các mục đổi hành vi người dùng thấy (ví dụ `check_url` coi 403/429 là "chưa rõ" thay vì "link chết") ghi rõ trong commit và `task-tracking.md`.
- Migration 0010: thêm bước khử trùng lặp trước khi tạo unique index — chỉ áp được nếu chưa có DB nào đã chạy 0010; ngược lại tạo migration mới. Builder kiểm tra và chọn.

**Đợt 3 — xuất HTML in được (T-604)**
- Route `GET /runs/{run_id}/report.html`, cùng quyền truy cập và dữ liệu với `report.md` (`services/report.py`), một trang HTML độc lập (không htmx, không JS), CSS in (`@media print`), dùng font/màu có sẵn trong `web/static` và quy ước `.stitch/DESIGN.md`. Nút "Tải bản in" cạnh nút Markdown hiện có. Bố cục chi tiết do builder quyết theo DESIGN.md.
- Không tạo PDF phía server (người dùng in ra PDF từ trình duyệt).

**Đợt 4 — kiểm tra live và coverage** (audit A-5, A-6)
- Thêm `pytest-cov` vào dev deps; CI in báo cáo coverage, không đặt ngưỡng.
- Viết sẵn `docs/live-check.md`: các bước chạy `CITEPROOF_LIVE_LLM=1`, `CITEPROOF_LIVE_GROBID=1` và một bài thật qua UI, ghi chi phí mỗi bài.
- **Thực thi đợt 4 cần cấu hình LLM.** Hiện chưa có key và máy chưa có Ollama: phần chạy thật được ghi là *chưa chạy* trong báo cáo cuối, không được báo là đạt. GROBID live chạy được ngay (container `grobid` đã có).

## 7. Thứ tự

1. Đợt 1.
2. Provider đa nhà cung cấp (mục 5).
3. Đợt 2.
4. Đợt 3.
5. Đợt 4 (phần không cần key; phần cần key để lại kèm hướng dẫn).

Mỗi bước kết thúc bằng `make check` và `CITEPROOF_REQUIRE_DB=1 uv run pytest -q` xanh.

## 8. Để builder quyết

- Vị trí kiểm tra `LLM_PROVIDER_CHANGED` trong worker; chặn hay ẩn nút ở báo cáo cũ (5.4).
- Cách backfill/so khớp `consent_llm_provider` NULL (5.4a), miễn đúng quy tắc đã nêu.
- Cách xử lý `arguments` không phải JSON (5.3), nếu có đường tốt hơn `{}`.
- Bố cục trang in (đợt 3), trong khuôn DESIGN.md.
- Chi tiết sửa từng mục P1 và cách xử lý migration 0010 (đợt 2).
- Cấu trúc thư mục test và fixture cho contract test.
