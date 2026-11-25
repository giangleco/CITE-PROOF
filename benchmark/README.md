# Đánh giá (T-506)

Harness chạy đúng các stage sản phẩm trên một database riêng (spec lõi §11, `07a` TK-15): dữ liệu
được nhập thành dòng thật dưới người dùng hệ thống `eval@citeproof.local`, rồi worker chạy S6–S9
(claim, D1–D4) hoặc S1–S3 (tài liệu tham khảo, D5). Không có đường mã riêng cho đánh giá.

## Chuẩn bị

Harness chỉ chạy trên database có tên kết thúc `_eval` hoặc `_test`, đọc từ
`CITEPROOF_EVAL_DATABASE_URL` (mặc định `.../citeproof_eval`):

```bash
createdb -h localhost -U citeproof citeproof_eval
uv run alembic -x url=postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof_eval upgrade head
```

## Chạy

```bash
# Gọi LLM thật (cần CITEPROOF_ANTHROPIC_API_KEY) và ghi cache `eval:<tập>@<hash>`.
uv run python -m citeproof.evaluation run --dataset tests/fixtures/eval/mini.jsonl \
    --config benchmark/configs/v2-x2.yaml --mode read_write [--split dev] [--out benchmark/results] [--dump]

# Phát lại: không gọi LLM, chỉ đọc cache của một lần read_write trước đó.
uv run python -m citeproof.evaluation run ... --mode replay

# D5: trạng thái tài liệu tham khảo.
uv run python -m citeproof.evaluation run-refs --dataset <d5.jsonl> --mode read_write
```

`make eval` chạy tập mini ở chế độ `read_write`; `make eval-replay` phát lại nó.

Chế độ cache:

- `read_write`: dùng cache nếu có, thiếu thì gọi LLM và ghi lại.
- `replay` (hoặc `replay_only`): chỉ đọc cache. **Cần một lần `read_write` trước đó** với cùng bộ
  dữ liệu (cùng hash), cùng cấu hình, prompt và model. Thiếu một entry nào thì lần chạy FAILED
  (`CacheMissError`) và lệnh thoát mã 1; replay trên cache rỗng luôn thất bại.
- `fresh`: luôn gọi LLM, không đọc cũng không ghi cache.

Metadata cache (S3) trên DB đánh giá không có TTL, nên kết quả không đổi theo thời gian.

## Cấu hình biến thể

`benchmark/configs/*.yaml` ghi đè `RunConfig` (mặc định lấy từ `Settings` như một lần chạy thật);
khóa lạ bị từ chối. Bảng (`models`, `effort`, `limits`, …) chỉ ghi đè khóa được nêu.

| Tệp | Biến thể |
|---|---|
| `v0.yaml` | V0: W5 closed-book (không đọc nguồn) |
| `v1.yaml` | V1: W5 trên abstract |
| `v2-x1.yaml` | V2, truy xuất bằng claim gốc |
| `v2-x2.yaml` | V2, thêm bản dịch W3 cho claim không phải tiếng Anh |
| `v3.yaml` | V3: tách ý W4, truy xuất và W5 theo từng ý |

## Bộ dữ liệu

JSONL theo `docs/13-testing-strategy.md` §B.3, thêm `source_path` (tương đối với thư mục của tệp
JSONL) hoặc `source_text` cho nguồn của claim; nguồn lưu thành `sources/open/<sha256>.txt`
(`OPEN_ACCESS`). D5: `{"id", "raw_reference", "expected_status", "split", "origin": "D5"}`. Bộ dữ
liệu thật đặt trong `benchmark/datasets/` (không commit).

## Kết quả

`benchmark/results/<YYYY-MM-DD>-<cấu hình>/` (D5: `<YYYY-MM-DD>-refs-<tập>/`), không commit:

- `report.json`, `report.md`: Macro-F1 (kèm khoảng tin cậy bootstrap 1000 lần, seed 0), F1 từng
  lớp, ma trận nhầm lẫn, Recall@k/MRR khi có `evidence_spans`, tỷ lệ quote bị loại, chi phí và độ
  trễ p50/p95 từ `llm_calls`; D5: accuracy, tỷ lệ tài liệu thật bị báo chưa tìm thấy, tỷ lệ tài
  liệu tạo giả được phát hiện. Chỉ chứa id mục và nhãn, không chứa văn bản.
- `config.yaml`: `RunConfig` đã đóng băng của lần chạy.
- `manifest.json`: commit, hash bộ dữ liệu, model, phiên bản prompt, chế độ cache, thời điểm.
- `dump.sql.gz` (với `--dump`): `pg_dump` của DB đánh giá, SHA-256 ghi trong manifest. Cần lệnh
  `pg_dump` trên `PATH` (gói `postgresql-client`); thiếu thì lệnh dừng trước khi chạy
  (`PG_DUMP_NOT_FOUND`).
