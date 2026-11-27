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

Đặt khóa vào `.env` ở thư mục gốc (Compose tự nạp, tệp không bắt buộc phải có):

```bash
echo 'CITEPROOF_ANTHROPIC_API_KEY=sk-ant-...' >> .env
docker compose up -d --build
```

Không có khóa thì các bước dùng LLM hạ cấp về luật và lần chạy có cảnh báo `LLM_NOT_CONFIGURED`.

### GROBID

GROBID đã chạy sẵn trong compose (cổng 8070, worker gọi qua `http://grobid:8070`); không cần cài thêm. Khi GROBID không truy cập được, bước đọc PDF dùng `pypdfium2` dự phòng.

### Đánh giá

```bash
make eval          # bộ đánh giá mini; gọi LLM thật (cần khóa) và ghi cache
make eval-replay   # phát lại cache đó, không gọi LLM
```

## Phát triển

```bash
make check                                # ruff, ruff format --check, mypy strict
CITEPROOF_REQUIRE_DB=1 uv run pytest -q   # cần db đang chạy
uv run python scripts/ui_snapshots.py --email E --password P --out /tmp/shots   # chụp màn hình các trang
```
