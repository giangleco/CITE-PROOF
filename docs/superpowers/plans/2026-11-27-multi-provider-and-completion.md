# LLM đa nhà cung cấp và hoàn thiện sau audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chọn nhà cung cấp LLM bằng cấu hình (`anthropic` | `openai_compatible`), an toàn với lần chạy cũ và sự đồng ý của người dùng; làm nốt đợt 1–4 của `docs/completion-audit.md`.

**Architecture:** Thêm `OpenAICompatibleProvider` sau protocol `LLMProvider` có sẵn. Định dạng khối nội bộ (kiểu Anthropic) giữ nguyên; provider mới chuyển đổi hai chiều. Định danh provider (`anthropic` hoặc `openai_compatible:<host>`) là nguồn duy nhất cho cache key, `RunConfig.provider` và `users.consent_llm_provider`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, Pydantic v2, SDK `anthropic`, SDK `openai` (mới), pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-11-27-multi-provider-and-completion-design.md` (đọc cùng plan). Audit: `docs/completion-audit.md`.

## Global Constraints

- Nhánh làm việc: `feat/multi-provider-completion` tách từ `main`. Không xóa/đẩy nhánh `feat/core-pipeline`.
- Trước khi viết mã dùng SDK `openai` hoặc `anthropic`: tra context7 (`/openai/openai-python`) cú pháp hiện hành.
- Mỗi task kết thúc: `make check` sạch và `CITEPROOF_REQUIRE_DB=1 uv run pytest -q` xanh (DB: `docker compose up -d db`).
- Commit kết thúc bằng dòng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Chữ hiển thị cho người dùng: tiếng Việt, đặt trong `src/citeproof/web/labels.py` và ghi vào `.stitch/DESIGN.md`.
- Thông điệp lỗi/log của provider chỉ gồm `<định danh> <TênLỗi> status=<mã>`; không bao giờ chứa prompt hay đầu ra.
- Không gửi tham số sampling (`temperature`, `top_p`, `top_k`) cho bất kỳ provider nào.
- Định danh Anthropic giữ đúng `anthropic` (cache key hiện có không được đổi).
- Test không gọi mạng; test live chỉ chạy khi có cờ `CITEPROOF_LIVE_LLM=1` / `CITEPROOF_LIVE_GROBID=1`.
- Tên lỗi mới: `LLM_PROVIDER_CHANGED`.

## Review Focus

1. `CITEPROOF_OPENAI_BASE_URL` có dấu `/` cuối, có path `/v1`, chữ hoa, cổng → cùng một định danh `openai_compatible:<host>[:port]` chữ thường (test ở Task 2).
2. Server tương thích không trả `usage`, hoặc trả `content: null` kèm `tool_calls` → không lỗi; token = 0, khối text bị bỏ (Task 3, Task 4).
3. `tool_calls[].function.arguments` không phải JSON → khối `tool_use` có `input == {}`, agent vẫn chạy tiếp (Task 4).
4. Run tạo khi chưa cấu hình LLM (`provider` = None), sau đó cấu hình provider → tiếp tục/kiểm chứng lại được, không `LLM_PROVIDER_CHANGED` (Task 6).
5. Người dùng đồng ý trước migration (`consent_llm_provider` NULL): provider hiện tại `anthropic` hoặc chưa cấu hình → không phải đồng ý lại; `openai_compatible:*` → phải đồng ý lại (Task 7).

---

## Đợt 1

### Task 1: Compose nhận `.env`, sửa tài liệu sai

**Files:**
- Modify: `docker-compose.yml` (service `migrate`, `app`, `worker`)
- Create: `tests/deploy/test_local_compose.py`
- Modify: `README.md`, `docs/project-status.md`, `docs/task-tracking.md`

**Interfaces:** Produces: không có API; Task 9 mở rộng README.

- [ ] **Step 1: Viết test hỏng** `tests/deploy/test_local_compose.py` (đọc YAML bằng `yaml.safe_load` như `test_prod_compose.py`):
  - `test_app_services_load_optional_dotenv`: với mỗi `migrate`, `app`, `worker`: `env_file == [{"path": ".env", "required": False}]`.
  - `test_container_urls_override_dotenv`: `environment["CITEPROOF_DATABASE_URL"]` chứa `@db:5432`; worker có `CITEPROOF_GROBID_URL == "http://grobid:8070"`.
  - `test_worker_can_reach_host_llm`: worker có `extra_hosts` chứa `"host.docker.internal:host-gateway"`.
- [ ] **Step 2:** `uv run pytest tests/deploy/test_local_compose.py -q` → FAIL.
- [ ] **Step 3:** Sửa `docker-compose.yml` theo test. Lưu ý anchor `&app-env` dùng chung; `env_file` phải nằm ở cấp service, không nằm trong anchor `environment`.
- [ ] **Step 4:** Test PASS; `docker compose config -q` exit 0.
- [ ] **Step 5: Tài liệu.**
  - README: xóa câu "Các bước xử lý thật (đọc bài, tra cứu, đối chiếu) chưa có…"; thêm mục "Bật LLM" (đặt `CITEPROOF_ANTHROPIC_API_KEY` trong `.env`, rồi `docker compose up -d --build`; không có khóa thì các bước LLM hạ cấp, cảnh báo `LLM_NOT_CONFIGURED`), mục GROBID (đã chạy trong compose), `make eval`/`make eval-replay`.
  - `docs/project-status.md`: cập nhật ngày 27/11/2026, phase hiện tại 6 (Implementation) — MVP mã hoàn tất trên `main`, các task còn mở lấy từ `task-tracking.md`; thêm dòng lịch sử.
  - `docs/task-tracking.md`: thay mọi "nhánh `feat/core-pipeline`" bằng "trên `main`"; cập nhật ngày.
- [ ] **Step 6:** `make check`, full pytest. Commit `fix(compose): load .env in app containers; refresh stale docs`.

---

## Provider đa nhà cung cấp

### Task 2: Cấu hình provider, định danh, validator

**Files:**
- Modify: `src/citeproof/core/settings.py`, `.env.example`, `.env.production.example`
- Create: `src/citeproof/llm/identity.py`
- Test: `tests/core/test_settings_llm_provider.py`, `tests/llm/test_identity.py`

**Interfaces:**
- Produces:
  - `Settings.llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"`
  - `Settings.openai_base_url: str | None = None`, `openai_api_key: SecretStr | None = None`, `openai_timeout_seconds: float = 600.0`, `openai_max_retries: int = 2`, `openai_structured_output: Literal["json_schema", "json_object", "none"] = "json_schema"`, `openai_send_reasoning_effort: bool = False`
  - `Settings.llm_configured -> bool` (property): `anthropic` có khóa khác rỗng, hoặc `openai_compatible` có `openai_base_url`.
  - `citeproof.llm.identity.provider_identity(settings: Settings) -> str | None` — None khi `not settings.llm_configured`.
  - `citeproof.llm.identity.openai_identity(base_url: str) -> str` — `openai_compatible:<host>[:port]`.

- [ ] **Step 1: Test hỏng** `tests/llm/test_identity.py`:
  - `openai_identity("https://API.OpenAI.com/v1/") == "openai_compatible:api.openai.com"`
  - `openai_identity("http://localhost:11434/v1") == "openai_compatible:localhost:11434"`
  - `openai_identity("https://openrouter.ai/api/v1") == "openai_compatible:openrouter.ai"`
  - `provider_identity(Settings(anthropic_api_key="k")) == "anthropic"`; không khóa → `None`; `openai_compatible` + base_url → định danh tương ứng.
  - URL không có host (ví dụ `"localhost"` không scheme) → `ValueError`.
- [ ] **Step 2: Test hỏng** `tests/core/test_settings_llm_provider.py` (dựng `Settings(_env_file=None, ...)`):
  - `test_openai_requires_custom_models`: `llm_provider="openai_compatible"`, `openai_base_url` đặt, `llm_models` mặc định → `ValidationError`, thông điệp chứa `CITEPROOF_LLM_MODELS`.
  - `test_configured_models_must_have_prices`: model trong `llm_models` thiếu ở `llm_prices` khi `llm_configured` → `ValidationError` liệt kê tên model thiếu.
  - `test_local_zero_price_accepted`: `llm_prices={"qwen3:8b": (0, 0)}`, mọi task dùng `qwen3:8b` → hợp lệ.
  - `test_unconfigured_skips_price_check`: không khóa, model lạ không giá → hợp lệ (giữ hành vi chạy không LLM).
  - `test_openai_base_url_validated_at_startup`: base_url không có host → `ValidationError`.
- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Cài đặt. Validator gom vào `@model_validator(mode="after")` hiện có. Thiếu `llm_provider_name` khi `openai_compatible` → `logger.warning` (không chặn). `.env.example`/`.env.production.example`: thêm các biến mới, chú thích tiếng Việt, ví dụ cho OpenAI/Gemini (`https://generativelanguage.googleapis.com/v1beta/openai/`)/OpenRouter/Ollama.
- [ ] **Step 5:** PASS; `make check`; full pytest (validator mới có thể làm hỏng test dựng `Settings` với model lạ — sửa test bằng cách khai giá, không nới validator).
- [ ] **Step 6:** Commit `feat(llm): provider settings, identity and price validation`.

### Task 3: `OpenAICompatibleProvider.complete` (worker)

**Files:**
- Modify: `pyproject.toml` (thêm `openai>=X,<X+1` theo phiên bản lớn hiện hành tra từ context7/PyPI), `uv.lock`
- Create: `src/citeproof/llm/providers/openai_compatible.py`
- Test: `tests/llm/test_openai_compatible_provider.py`

**Interfaces:**
- Consumes: `openai_identity` (Task 2), `strict_output_schema` (`llm/schema.py`), `ProviderRequest`/`ProviderResponse` (`llm/types.py`).
- Produces:
  - `class OpenAICompatibleProvider` với `__init__(self, client: openai.AsyncOpenAI, *, identity: str, structured_output: Literal["json_schema","json_object","none"], send_reasoning_effort: bool)`, thuộc tính `name: str` (= identity), `async complete(request) -> ProviderResponse`, `async complete_turn(request) -> TurnResponse` (Task 4), `async aclose()`.
  - `map_finish_reason(reason: str | None, *, refused: bool) -> str` (module-level).

- [ ] **Step 1: Test hỏng.** Giả `AsyncOpenAI` bằng `httpx.MockTransport` truyền qua `http_client=httpx.AsyncClient(transport=...)` (ghi lại body JSON gửi đi). Test:
  - `test_request_shape_json_schema`: body có `messages[0] == {"role":"system","content":<system>}`, `response_format.type == "json_schema"`, `response_format.json_schema.strict is False`, `max_completion_tokens == request.max_tokens`; **không** có khóa `temperature`, `top_p`, `reasoning_effort`.
  - `test_json_object_mode_appends_schema_to_system`: `response_format == {"type":"json_object"}`; system chứa JSON của schema.
  - `test_none_mode_has_no_response_format`.
  - `test_reasoning_effort_sent_only_when_enabled`: bật cờ + `effort="high"` → `reasoning_effort == "high"`.
  - `test_response_mapping`: `choices[0].message.content` → `text`; `usage.prompt_tokens/completion_tokens` → token; `model` từ phản hồi; `finish_reason "stop"` → `stop_reason "end_turn"`.
  - `test_finish_reason_mapping` (parametrize): `length→max_tokens`, `content_filter→refusal`, `tool_calls→tool_use`, `stop→end_turn`, `"weird"→"weird"`; message có `refusal` khác rỗng → `refusal`.
  - `test_missing_usage_counts_zero`: phản hồi không có `usage` → 0/0, không lỗi.
  - `test_error_mapping` (parametrize): 429 → `RetryableError`, 503 → `RetryableError`, lỗi kết nối → `RetryableError`, 400 → `ExternalServiceError`; `str(exc)` bắt đầu bằng định danh và không chứa chuỗi system/user của request.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Cài đặt theo spec §5.3 (Chat Completions, không luồng). `name` dùng cho cache key nên phải bằng `identity`.
- [ ] **Step 4:** PASS; `make check` (mypy strict với kiểu của SDK `openai`).
- [ ] **Step 5:** Commit `feat(llm): OpenAI-compatible provider for structured worker calls`.

### Task 4: `OpenAICompatibleProvider.complete_turn` (agent)

**Files:**
- Modify: `src/citeproof/llm/providers/openai_compatible.py`
- Test: `tests/llm/test_openai_compatible_turns.py`

**Interfaces:**
- Consumes: `TurnRequest`/`TurnResponse`, `ToolSpec` (`llm/types.py`); định dạng khối mà `AgentRunner` dùng (`agent_runner.py:283-357`, `_tool_result`, `_text`).
- Produces: `to_chat_messages(system: str, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]`, `from_chat_message(message: Any) -> tuple[dict[str, Any], ...]` (module-level, thuần).

- [ ] **Step 1: Test hỏng** (bảng chuyển đổi ở spec §5.3):
  - `test_user_string_message`, `test_user_text_blocks_joined`.
  - `test_tool_results_become_tool_messages_before_user_text`: user content `[tool_result(id=a), tool_result(id=b), text("nudge")]` → `[{"role":"tool","tool_call_id":"a",...}, {"role":"tool","tool_call_id":"b",...}, {"role":"user","content":"nudge"}]`.
  - `test_assistant_text_and_tool_use`: → `{"role":"assistant","content":"…","tool_calls":[{"id","type":"function","function":{"name","arguments":json.dumps(input)}}]}`; assistant chỉ có tool_use → `content is None`.
  - `test_thinking_and_unknown_blocks_dropped`.
  - `test_from_chat_message`: content + 2 tool_calls → `({"type":"text",...}, {"type":"tool_use","id","name","input":{...}}, ...)`; `content None` → không có khối text.
  - `test_invalid_arguments_json_gives_empty_input` (Review Focus 3).
  - `test_tools_request_shape`: `tools[i] == {"type":"function","function":{"name","description","parameters":strict_output_schema(...)}}`, không có `strict`; không có `tool_choice`.
  - `test_agent_runner_roundtrip`: chạy `AgentRunner` thật (fixture có sẵn trong `tests/llm/test_agent_runner.py`) với provider này trên MockTransport kịch bản 2 lượt (gọi một công cụ giả rồi submit) → kết quả `OK`, lượt 2 nhận đúng `role:"tool"` cho id lượt 1.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Cài đặt. **Step 4:** PASS + `make check`.
- [ ] **Step 5:** Commit `feat(llm): agent turns on the OpenAI-compatible provider`.

### Task 5: `make_provider`, contract test, test live chung, ADR-011

**Files:**
- Modify: `src/citeproof/llm/factory.py`, `src/citeproof/pipeline/deps.py` (nếu gọi `make_anthropic_provider` trực tiếp)
- Create: `tests/llm/contract/__init__.py`, `tests/llm/contract/test_provider_contract.py`
- Rename: `tests/llm/test_anthropic_live.py` → `tests/llm/test_llm_live.py`
- Create: `docs/decisions/ADR-011-multi-provider-llm.md`; Modify: `docs/decisions/ADR-006-llm-access-layer.md` (dòng trạng thái "Superseded in part by ADR-011"), `docs/07-technical-architecture.md` §4.2 (một đoạn)

**Interfaces:**
- Consumes: Task 2–4.
- Produces: `make_provider(settings: Settings) -> LLMProvider | None` (None khi `not settings.llm_configured`); `make_llm_client`/`make_agent_runner` dùng nó.

- [ ] **Step 1: Test hỏng** `tests/llm/test_factory.py` (hoặc thêm vào file có sẵn): `make_provider` trả `AnthropicProvider` / `OpenAICompatibleProvider` (với `name` = định danh) / `None`; `openai_api_key` trống → SDK vẫn dựng được (khóa giả `"unused"`).
- [ ] **Step 2: Contract test** tham số hóa `["anthropic", "openai_compatible"]`, mỗi tham số có fixture dựng provider trên SDK giả (tái dùng stub của `tests/llm/test_anthropic_provider.py` cho Anthropic, MockTransport cho OpenAI) với một "kịch bản" trung lập (JSON trả về, lý do dừng, tool call, lỗi HTTP). Hợp đồng:
  - worker: JSON hợp lệ → `text` parse được; token > 0 khi có usage; `stop_reason` ∈ {`end_turn`,`max_tokens`,`refusal`} theo kịch bản.
  - agent: lượt có tool call → khối `tool_use` có `id`,`name`,`input` dict; gửi lại cùng `tool_result` → request lượt 2 hợp lệ với provider đó.
  - lỗi: 429/5xx/mất kết nối → `RetryableError`; 4xx → `ExternalServiceError`; thông điệp không chứa prompt.
  - `provider.name` không rỗng và ổn định qua hai lần dựng cùng cấu hình.
- [ ] **Step 3:** Run → FAIL; cài `make_provider`; PASS.
- [ ] **Step 4:** `test_llm_live.py`: marker `live`, skip trừ khi `CITEPROOF_LIVE_LLM=1` và `Settings().llm_configured`; một lời gọi structured output nhỏ qua `make_llm_client` và một lượt agent một công cụ qua `make_agent_runner`, dùng model của task `reference.parse`.
- [ ] **Step 5:** ADR-011 theo `docs/ADR-template.md`: context (chưa chọn hãng, phụ thuộc nhà đầu tư), phương án A/B/C của spec §4, quyết định B, hệ quả (không fallback/prompt caching ngoài Anthropic; `strict:false`; định danh theo host; đồng ý lại).
- [ ] **Step 6:** `make check`, full pytest. Commit `feat(llm): select provider by config; provider contract tests; ADR-011`.

### Task 6: Đóng băng provider vào lần chạy; chặn báo cáo cũ

**Files:**
- Modify: `src/citeproof/pipeline/config.py` (`RunConfig`), nơi tạo run (`RunConfig.for_new_run` hoặc tên tương ứng ở `config.py:140`), `src/citeproof/pipeline/worker.py` hoặc `pipeline/deps.py` (kiểm tra khi stage cần LLM), `src/citeproof/services/reverify.py`, `src/citeproof/core/errors.py` (nếu mã lỗi khai báo ở đó), `src/citeproof/web/labels.py`, template báo cáo có nút kiểm chứng lại/tra cứu lại/tải nguồn, `.stitch/DESIGN.md`
- Test: `tests/pipeline/test_run_provider.py`, `tests/services/test_reverify_provider.py`, `tests/web/test_report_provider_changed.py`

**Interfaces:**
- Consumes: `provider_identity` (Task 2).
- Produces:
  - `RunConfig.provider: str | None` (mặc định None; `RunConfig.load` của cấu hình cũ không có khóa → None — chọn đơn giản hóa spec §5.4 vì máy chưa từng có key; ghi điều này trong commit).
  - `ensure_same_provider(config: RunConfig, settings: Settings) -> None` — ném `ConflictError(code="LLM_PROVIDER_CHANGED")` khi `config.provider is not None` và khác `provider_identity(settings)`.
  - `provider_changed(config: RunConfig, settings: Settings) -> bool` cho template.

- [ ] **Step 1: Test hỏng:**
  - run tạo khi có khóa Anthropic → `config.provider == "anthropic"`; không khóa → `None`.
  - worker: run `provider="anthropic"`, settings `openai_compatible` → stage cần LLM kết thúc với lỗi không thử lại mã `LLM_PROVIDER_CHANGED` (job không retry); run `provider=None` + settings bất kỳ → chạy bình thường (Review Focus 4).
  - reverify/retry-lookups/upload-missing: provider đổi → `ConflictError` mã `LLM_PROVIDER_CHANGED`, không tạo run con.
  - web: trang báo cáo của run có provider khác → không hiện nút "Kiểm chứng lại"/"Tra cứu lại"/form tải nguồn, hiện câu `labels` "Nhà cung cấp mô hình đã đổi; hãy tạo lần kiểm tra mới cho tài liệu này."; `report.md` vẫn tải được (200).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Cài đặt; chỗ kiểm tra trong worker đặt sao cho stage không cần LLM (ví dụ S9) không bị chặn — builder chọn (spec §8). **Step 4:** PASS.
- [ ] **Step 5:** `make check`, full pytest. Commit `feat(runs): freeze LLM provider per run; block stale reverify`.

### Task 7: Đồng ý lại khi đổi provider

**Files:**
- Create: `migrations/versions/0011_consent_llm_provider.py`
- Modify: `src/citeproof/db/models/users.py`, `src/citeproof/services/auth.py` (`needs_consent` ~dòng 219, hàm ghi đồng ý ~223), `src/citeproof/evaluation/importer.py:147`
- Test: `tests/services/test_consent_provider.py`, `tests/db/test_migrations.py` (nếu có test upgrade/downgrade, thêm 0011)

**Interfaces:**
- Consumes: `provider_identity`.
- Produces: cột `users.consent_llm_provider: Mapped[str | None]`; `needs_consent(user, settings) -> bool` giữ chữ ký hiện có.

- [ ] **Step 1: Test hỏng** (Review Focus 5):
  - đồng ý xong → `consent_llm_provider == provider_identity(settings)`.
  - đã đồng ý với `anthropic`, settings đổi sang `openai_compatible:x` → `needs_consent` True; đồng ý lại → False.
  - `consent_llm_provider` NULL + hiện tại `anthropic` hoặc None → False (nếu `consent_llm_at` còn mới); NULL + `openai_compatible:*` → True.
  - quy tắc cũ `consent_text_updated_at` vẫn đúng.
  - migration 0011 upgrade/downgrade chạy được.
- [ ] **Step 2–4:** FAIL → cài → PASS.
- [ ] **Step 5:** Commit `feat(auth): require re-consent when the LLM provider changes`.

### Task 8: Bộ đánh giá chạy trên mọi provider

**Files:**
- Modify: `src/citeproof/evaluation/runner.py` (`ReplayOnlyProvider`, `_provider_for` ~dòng 233–262), `src/citeproof/evaluation/__main__.py` (tùy chọn `--provider-identity`), phần ghi `report.md` của đánh giá
- Test: `tests/evaluation/test_runner_provider.py`

**Interfaces:**
- Produces: `ReplayOnlyProvider(name: str)`; `_provider_for(settings, mode, provider, *, identity_override: str | None = None)`.

- [ ] **Step 1: Test hỏng:**
  - `read_write` không cấu hình LLM → `InputError` mã `LLM_NOT_CONFIGURED`, thông điệp không nhắc `ANTHROPIC`.
  - `read_write` với `openai_compatible` → trả None để `make_llm_client` dựng provider qua `make_provider` (hoặc trả provider; giữ nhất quán với code hiện tại).
  - `replay` → `ReplayOnlyProvider.name == provider_identity(settings) or "anthropic"`; `--provider-identity openai_compatible:x` ghi đè.
  - `report.md` đánh giá có dòng định danh provider.
- [ ] **Step 2–4:** FAIL → cài → PASS.
- [ ] **Step 5:** Commit `feat(eval): run and replay evaluations on any provider`.

### Task 9: Tài liệu cấu hình provider

**Files:** Modify `README.md` (mục "Bật LLM"), `docs/14-deployment-devops.md`, `docs/task-tracking.md` (thêm dòng T-109b hoặc ghi vào T-107: đa nhà cung cấp COMPLETED).

- [ ] **Step 1:** README + `docs/14`: bảng cấu hình mẫu cho Anthropic, OpenAI, Gemini, OpenRouter, Ollama (biến `CITEPROOF_LLM_PROVIDER`, `CITEPROOF_OPENAI_BASE_URL`, `CITEPROOF_LLM_MODELS`, `CITEPROOF_LLM_PRICES`, `CITEPROOF_LLM_PROVIDER_NAME`, `CITEPROOF_LLM_PROVIDER_POLICY_URL`); ghi chú Ollama trong Docker dùng `http://host.docker.internal:11434/v1`; hệ quả đổi provider (báo cáo cũ bị chặn kiểm chứng lại, người dùng phải đồng ý lại, cache tách riêng). Mục "Thêm một provider mới": cài `LLMProvider`, thêm vào contract test, đăng ký trong `make_provider`, thêm giá trị `llm_provider`.
- [ ] **Step 2:** Kiểm tra mọi biến môi trường nhắc trong tài liệu có trong `Settings` (`grep`). Commit `docs: configure LLM providers`.

---

## Đợt 2

### Task 10: Phân loại việc hoãn

**Files:** Modify `docs/completion-audit.md` (thêm §5 "Phân loại việc hoãn").

- [ ] **Step 1:** Với từng dòng `minor (deferred)` trong `docs/superpowers/handoff/2026-11-24-core-pipeline-handoff.md` (Phụ lục B) và `2026-11-25-core-pipeline-completion.md`, cùng 4 mục "parked" của final review: kiểm tra mã hiện tại, ghi một dòng bảng `| Nguồn | Mục (rút gọn) | Phân loại | Ghi chú |` với phân loại ∈ `đã xử lý` / `sửa trong Task 11–14` / `để sau` / `chấp nhận (giới hạn)`.
- [ ] **Step 2:** Commit `docs(audit): triage deferred review items`.

### Task 11: DB và job

**Files:** Modify `migrations/versions/0010_source_access.py`, `src/citeproof/pipeline/jobs.py` (~dòng 104–111 và `stale_exhausted` ~193); Test `tests/db/test_migration_0010_dedupe.py`, `tests/pipeline/test_jobs.py`.

- [ ] **Step 1: Test hỏng:**
  - migration: ở DB test hạ xuống 0009, chèn hai dòng `source_files` `origin='OPEN_ACCESS'` trùng `(work_id, sha256)`, nâng lên 0010 → thành công, còn một dòng (giữ dòng `created_at` sớm nhất; dòng `source_chunks`/evidence trỏ dòng bị xóa được chuyển sang dòng giữ lại hoặc theo `ON DELETE` hiện có — builder kiểm FK và chọn, ghi lý do trong commit).
  - jobs: job `RUNNING` với `lease_expires_at IS NULL` và `started_at` cũ hơn `job_lock_timeout_seconds` → được claim lại (còn lượt) / tính vào `stale_exhausted` (hết lượt).
- [ ] **Step 2–4:** FAIL → cài (`coalesce(lease_expires_at, started_at + lock_timeout)`; dedupe bằng `DELETE ... USING` trước `create_index`) → PASS.
- [ ] **Step 5:** Commit `fix(db,jobs): dedupe before 0010 unique index; reclaim NULL-lease jobs`.

### Task 12: Tra cứu metadata và URL

**Files:** Modify `src/citeproof/integrations/metadata/base.py` (`parse_records`), `src/citeproof/integrations/metadata/cache.py` (`query_hash` payload), `src/citeproof/modules/references/web_check.py` (`check_url`); Test tương ứng trong `tests/integrations/metadata/`, `tests/modules/references/`.

**Interfaces:** `METADATA_CACHE_VERSION: int = 2` trong `cache.py`, được thêm vào payload băm (`{"v": 2, "op", "args"}`).

- [ ] **Step 1: Test hỏng:**
  - nguồn trả 3 mục, cả 3 bị `parse_record` bỏ → kết quả tra cứu là `error` (không phải `not_found`) và **không** ghi cache; trả 0 mục → vẫn `not_found` như cũ.
  - `query_hash({"op":"doi","args":...})` khác giá trị trước khi đổi (ghim hash mới); hai payload giống nhau → cùng hash.
  - `check_url`: 403 → None, 429 → None, 404 → False, 410 → False, 500 → False, 200/301 → True.
- [ ] **Step 2–4:** FAIL → cài → PASS. Hành vi `check_url` đổi (403/429 thành "chưa rõ"): cập nhật docstring và `task-tracking.md` T-303.
- [ ] **Step 5:** Commit `fix(metadata): don't cache all-dropped responses; versioned cache key; 403/429 URL unknown`.

### Task 13: Truy xuất và kiểm chứng

**Files:** Modify `src/citeproof/modules/retrieval/translate.py` (`numbers_preserved`), `src/citeproof/modules/verification/quotes.py` (khớp quote), `src/citeproof/modules/verification/stage.py` (`_CostGate`); Test trong `tests/modules/retrieval/`, `tests/modules/verification/`.

**Interfaces:** `_CostGate.allow(reserve: Decimal) -> bool` và `_CostGate.release(reserve: Decimal) -> None`; `reserve` của một cặp = giá đầu ra của model `verdict.judge` × `max_tokens["verdict.judge"]` / 1e6 (từ `llm_prices` của `RunConfig`/settings; model không có giá → 0).

- [ ] **Step 1: Test hỏng:**
  - `numbers_preserved("năm 2012", "in 12")` False; `numbers_preserved("95,7%", "95.7 percent")` True; `numbers_preserved("12 và 2012", "2012 and 12")` True (so tập số, không chuỗi con).
  - quote `"95.7%"` không khớp trong chunk chỉ có `"195.7%"`; khớp trong `"đạt 95.7%."`; quote bắt đầu/kết thúc bằng dấu câu vẫn khớp như cũ.
  - gate: limit 1.00, đã tiêu 0.50, reserve 0.30, concurrency 3 → chỉ 1 cặp được `allow` cùng lúc trước khi `release`; sau `release` và cost thật 0.10 → cặp tiếp theo được phép.
  - Zero-hit (mục hoãn Task 14 cũ): viết test chứng minh S8 xử lý cặp không có dòng `retrieval_results` giống cặp 0 kết quả (precheck → kết quả tất định, không gọi LLM). Nếu test qua ngay: ghi "chấp nhận" trong bảng Task 10 với lý do (truy xuất lexical tất định, W3 đã cache). Nếu hỏng: sửa precheck.
- [ ] **Step 2–4:** FAIL → cài → PASS.
- [ ] **Step 5:** Commit `fix(verification): number-set check, quote word boundaries, cost reservation`.

### Task 14: Web và triển khai

**Files:** Modify `src/citeproof/web/__init__.py` (thứ tự middleware ~dòng 146–171), `scripts/smoke.py`, `src/citeproof/core/settings.py` (validator `env == "pilot"`); Test `tests/web/test_ratelimit.py`, `tests/deploy/test_smoke.py`, `tests/core/test_settings_pilot.py`.

- [ ] **Step 1: Test hỏng:**
  - phản hồi 429 có các header của `SecurityHeadersMiddleware` (ví dụ `x-content-type-options: nosniff`, CSP).
  - `smoke.py`: không còn `--password`; mật khẩu lấy từ biến `CITEPROOF_SMOKE_PASSWORD`, thiếu thì `getpass`; parser không có option `--password`.
  - `Settings(env="pilot", ...)` với bất kỳ trường chuỗi/SecretStr/URL nào chứa `CHANGE_ME_` → `ValidationError` liệt kê tên biến; `env="local"` không kiểm.
- [ ] **Step 2–4:** FAIL → cài → PASS. Cập nhật `docs/14` (cách gọi smoke mới).
- [ ] **Step 5:** Commit `fix(web,deploy): security headers on 429; no password in argv; reject CHANGE_ME_ in pilot`.

---

## Đợt 3

### Task 15: Xuất báo cáo HTML in được (T-604)

**Files:** Modify `src/citeproof/services/report.py` (tách dữ liệu dùng chung với `render_markdown` ~dòng 478 nếu cần), `src/citeproof/web/routes/report.py` (route mới cạnh `report.md` ~dòng 284); Create `src/citeproof/web/templates/pages/report_print.html`; Modify template báo cáo có nút tải Markdown, `.stitch/DESIGN.md`, `docs/task-tracking.md` (T-604 COMPLETED); Test `tests/web/test_report_print.py`.

**Interfaces:** `GET /runs/{run_id}/report.html` → `text/html`, `Content-Disposition: inline`.

- [ ] **Step 1: Test hỏng:**
  - chủ sở hữu → 200, chứa tên tài liệu, đủ 4 phần như `report.md` (tổng quan, tài liệu tham khảo, nhận định, còn thiếu) và mọi quote bằng chứng có trong `report.md`.
  - người khác → 404 (như `report.md`); chưa đăng nhập → chuyển tới `/login`.
  - không có `<script` và không có `hx-`; có `@media print`.
  - nội dung người dùng được escape (tiêu đề chứa `<b>` hiện nguyên văn).
  - trang báo cáo có link "Tải bản in" tới route mới.
- [ ] **Step 2–4:** FAIL → cài → PASS.
- [ ] **Step 5:** Commit `feat(report): printable HTML export (T-604)`.

---

## Đợt 4

### Task 16: Coverage, hướng dẫn kiểm tra live, chốt trạng thái

**Files:** Modify `pyproject.toml` (dev: `pytest-cov`), `uv.lock`, `.github/workflows/ci.yml` (pytest thêm `--cov=citeproof --cov-report=term`), Create `docs/live-check.md`; Modify `docs/completion-audit.md` (§6 "Kết quả sau khi làm"), `docs/project-status.md`, `docs/task-tracking.md`.

- [ ] **Step 1:** Thêm `pytest-cov`; chạy `CITEPROOF_REQUIRE_DB=1 uv run pytest -q --cov=citeproof --cov-report=term | tail -5`, ghi tổng % vào audit §6.
- [ ] **Step 2:** `docs/live-check.md`: các bước (a) cấu hình provider theo README, (b) `CITEPROOF_LIVE_LLM=1 uv run pytest -m live -q`, (c) `docker compose up -d grobid` rồi `CITEPROOF_LIVE_GROBID=1 uv run pytest -m grobid -q`, (d) chạy một bài thật qua UI, đọc chi phí ở "Chi tiết kỹ thuật", ghi vào bảng có sẵn trong file.
- [ ] **Step 3:** Chạy thật (c) GROBID live. Ghi kết quả nguyên văn vào audit §6. Chạy (b) chỉ khi `Settings().llm_configured`; không thì ghi "chưa chạy: chưa cấu hình LLM".
- [ ] **Step 4:** Cập nhật audit §6 (bảng: mục audit → task → trạng thái đã kiểm chứng bằng lệnh nào), `project-status.md`, `task-tracking.md`.
- [ ] **Step 5:** `make check`, full pytest. Commit `chore: coverage in CI, live-check guide, final status`.
