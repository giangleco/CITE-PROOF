# CiteProof AI

Kiểm tra trích dẫn cho khóa luận: tài liệu tham khảo nào sai thông tin hoặc chưa tìm thấy, nhận định nào có thể không được nguồn hỗ trợ, kèm bằng chứng nguyên văn. Tài liệu dự án ở [`docs/`](docs/README.md).

## Chạy trên máy

```bash
docker compose up -d --build          # db, grobid, migrate, app (cổng 8000), worker
```

Mở http://127.0.0.1:8000. Tạo tài khoản bằng CLI (mật khẩu nhập ẩn hai lần):

```bash
uv run python -m citeproof.cli create-user --email ban@vi.du --name "Tên bạn" --admin
uv run python -m citeproof.cli create-invite --days 14    # mã mời cho người khác đăng ký ở /register
uv run python -m citeproof.cli seed-sample --email ban@vi.du   # thêm bài mẫu đủ mọi trạng thái
```

CLI chạy trên máy host, nối tới DB ở `localhost:5432` (mặc định của `CITEPROOF_DATABASE_URL`).

### Thực nghiệm giao diện

- **Bài mẫu**: nút "Thêm bài mẫu" ở trang "Tài liệu của tôi", hoặc lệnh `seed-sample`. Chỉ có khi `CITEPROOF_ENV=local`.
- **Chế độ thử** ở form "Kiểm tra bài mới" (chỉ local): đặt thời gian mỗi bước và bước giả lập lỗi để xem trang tiến độ, lỗi và "Tiếp tục từ bước N".
- Tài khoản `--admin` thấy thêm lựa chọn biến thể và phần "Chi tiết kỹ thuật".
- Khi chạy sau reverse proxy (pilot), đặt `CITEPROOF_TRUSTED_PROXIES` là IP của proxy; nếu không, mọi người dùng chung một IP và giới hạn đăng nhập theo IP sẽ chặn cả lớp.

### Bật LLM

Chỉ một provider LLM mỗi lần triển khai, chọn bằng `CITEPROOF_LLM_PROVIDER`: `anthropic` (mặc định) hoặc `openai_compatible` (mọi máy chủ có API Chat Completions kiểu OpenAI: OpenAI, Gemini, OpenRouter, Ollama...). Đặt biến vào `.env` ở thư mục gốc (Compose tự nạp, tệp không bắt buộc phải có), rồi `docker compose up -d --build`.

**Anthropic** (mặc định):

```bash
echo 'CITEPROOF_ANTHROPIC_API_KEY=sk-ant-...' >> .env
```

**Tương thích OpenAI**: đặt `CITEPROOF_LLM_PROVIDER=openai_compatible`, `CITEPROOF_OPENAI_BASE_URL` (và `CITEPROOF_OPENAI_API_KEY` nếu máy chủ cần khóa), cùng `CITEPROOF_LLM_MODELS` và `CITEPROOF_LLM_PRICES`.

`CITEPROOF_LLM_MAX_TOKENS` (mặc định 16000 cho `verdict.*` và `agent.*`) phải nằm trong giới hạn token đầu ra của model đã chọn, nếu không provider trả 400 và tác vụ ghi `PROCESSING_ERROR`.

`CITEPROOF_RUN_MAX_COST_USD` phải lớn hơn một lần giữ chỗ chi phí của W5 (giá đầu ra x `max_tokens` của `verdict.judge`, khoảng 0,16 USD với mặc định); nếu thấp hơn, không lệnh gọi W5 nào chạy và các cặp nhận `COST_LIMIT_REACHED`.

| Nhà cung cấp | `CITEPROOF_OPENAI_BASE_URL` | Ghi chú |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | Cần `CITEPROOF_OPENAI_API_KEY` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | Cần khóa Gemini; tên model theo Gemini |
| OpenRouter | `https://openrouter.ai/api/v1` | Tên model dạng `hãng/model` |
| Ollama | `http://localhost:11434/v1` | Không cần khóa; giá `[0,0]`. Trong Docker dùng `http://host.docker.internal:11434/v1` (worker đã có `extra_hosts: host.docker.internal:host-gateway`) |

Quy tắc bắt buộc (server từ chối khởi động nếu vi phạm):

- **Đặt model cho TẤT CẢ tác vụ trong `CITEPROOF_LLM_MODELS`**, đủ 8 khóa (map này thay hẳn mặc định, không trộn): `reference.parse`, `claim.extract`, `claim.translate`, `claim.decompose`, `verdict.judge`, `verdict.judge_closed_book`, `agent.reference_resolver`, `agent.evidence_investigator`. Thiếu khóa nào hoặc thiếu giá của model nào thì khởi động báo lỗi nêu tên khóa/model đó (thiếu khóa cũng bị từ chối với Anthropic khi đã có khóa API).
- **Mọi model trong `CITEPROOF_LLM_MODELS` phải có giá** trong `CITEPROOF_LLM_PRICES`: JSON `{"model":[đầu_vào,đầu_ra]}`, USD trên 1 triệu token. Model chạy cục bộ khai `[0,0]`.
- Nên đặt `CITEPROOF_LLM_PROVIDER_NAME` và `CITEPROOF_LLM_PROVIDER_POLICY_URL` để màn hình đồng ý nêu đúng nhà cung cấp (thiếu tên thì chỉ có cảnh báo trong log).

Ví dụ OpenAI (chép nguyên vào `.env`; tên model và giá chỉ để minh họa, kiểm tra lại giá hiện hành của hãng):

```bash
CITEPROOF_LLM_PROVIDER=openai_compatible
CITEPROOF_OPENAI_BASE_URL=https://api.openai.com/v1
CITEPROOF_OPENAI_API_KEY=sk-...
CITEPROOF_LLM_MODELS='{"reference.parse":"gpt-4o-mini","claim.extract":"gpt-4o-mini","claim.translate":"gpt-4o-mini","claim.decompose":"gpt-4o-mini","verdict.judge":"gpt-4o","verdict.judge_closed_book":"gpt-4o","agent.reference_resolver":"gpt-4o","agent.evidence_investigator":"gpt-4o"}'
CITEPROOF_LLM_PRICES='{"gpt-4o-mini":[0.15,0.6],"gpt-4o":[2.5,10.0]}'
CITEPROOF_LLM_PROVIDER_NAME=OpenAI
CITEPROOF_LLM_PROVIDER_POLICY_URL=https://openai.com/policies/privacy-policy
```

Ví dụ Ollama chạy trong Docker (đã `ollama pull qwen2.5:14b`):

```bash
CITEPROOF_LLM_PROVIDER=openai_compatible
CITEPROOF_OPENAI_BASE_URL=http://host.docker.internal:11434/v1
CITEPROOF_LLM_MODELS='{"reference.parse":"qwen2.5:14b","claim.extract":"qwen2.5:14b","claim.translate":"qwen2.5:14b","claim.decompose":"qwen2.5:14b","verdict.judge":"qwen2.5:14b","verdict.judge_closed_book":"qwen2.5:14b","agent.reference_resolver":"qwen2.5:14b","agent.evidence_investigator":"qwen2.5:14b"}'
CITEPROOF_LLM_PRICES='{"qwen2.5:14b":[0,0]}'
CITEPROOF_LLM_PROVIDER_NAME="Ollama (máy chủ nội bộ)"
```

Biến tinh chỉnh (tùy chọn): `CITEPROOF_OPENAI_TIMEOUT_SECONDS` (600), `CITEPROOF_OPENAI_MAX_RETRIES` (2), `CITEPROOF_OPENAI_STRUCTURED_OUTPUT` (`json_schema` mặc định; `json_object` hoặc `none` cho máy chủ không hỗ trợ schema), `CITEPROOF_OPENAI_SEND_REASONING_EFFORT` (`false`). Fallback phía server khi model từ chối và prompt caching chỉ có ở Anthropic.

**Hệ quả khi đổi provider.** Định danh provider là `anthropic` hoặc `openai_compatible:<host>[:port]` (đổi host cũng là đổi provider). Mỗi lần chạy đóng băng provider lúc tạo, nên sau khi đổi: báo cáo cũ không kiểm chứng lại hoặc tiếp tục được (hiện thông báo yêu cầu tạo lần kiểm tra mới); người dùng phải đồng ý lại; cache LLM tách riêng theo định danh nên không dùng lại kết quả của provider cũ.

Không cấu hình provider nào (không có khóa Anthropic, hoặc `openai_compatible` thiếu `CITEPROOF_OPENAI_BASE_URL`) thì các bước dùng LLM hạ cấp về luật và lần chạy có cảnh báo `LLM_NOT_CONFIGURED`.

### Thêm một provider mới

1. Cài `LLMProvider` (`src/citeproof/llm/providers/base.py`) trong `src/citeproof/llm/providers/`.
2. Thêm vào contract test tham số hóa `tests/llm/contract/test_provider_contract.py` (SDK giả, không gọi mạng).
3. Đăng ký trong `make_provider` (`src/citeproof/llm/factory.py`), và cho định danh ở `src/citeproof/llm/identity.py`.
4. Thêm giá trị vào `llm_provider` trong `src/citeproof/core/settings.py` (cùng biến cấu hình riêng của provider).

### GROBID

GROBID đã chạy sẵn trong compose (cổng 8070, worker gọi qua `http://grobid:8070`); không cần cài thêm. Khi GROBID không truy cập được, bước đọc PDF dùng `pypdfium2` dự phòng.

### Đánh giá

```bash
make eval          # bộ đánh giá mini; gọi LLM thật (cần khóa, trừ server cục bộ) và ghi cache
make eval-replay   # phát lại cache đó, không gọi LLM
```

Bộ đánh giá chạy trên provider đang cấu hình. Để phát lại cache của provider khác mà không cần cấu hình nó (chỉ ở chế độ replay): `uv run python -m citeproof.evaluation run --dataset tests/fixtures/eval/mini.jsonl --config benchmark/configs/v2-x2.yaml --mode replay --provider-identity openai_compatible:<host>[:port]`.

## Phát triển

```bash
make check                                # ruff, ruff format --check, mypy strict
CITEPROOF_REQUIRE_DB=1 uv run pytest -q   # cần db đang chạy
uv run python scripts/ui_snapshots.py --email E --password P --out /tmp/shots   # chụp màn hình các trang
```
