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
- Các bước xử lý thật (đọc bài, tra cứu, đối chiếu) chưa có: bài bạn tải lên sẽ chạy hết 9 bước nhưng báo cáo trống.
- Tài khoản `--admin` thấy thêm lựa chọn biến thể và phần "Chi tiết kỹ thuật".

## Phát triển

```bash
make check                                # ruff, ruff format --check, mypy strict
CITEPROOF_REQUIRE_DB=1 uv run pytest -q   # cần db đang chạy
uv run python scripts/ui_snapshots.py --email E --password P --out /tmp/shots   # chụp màn hình các trang
```
