# Hướng dẫn kiểm tra live

Các kiểm tra dưới đây cần dịch vụ thật (LLM, GROBID) nên mặc định bị bỏ qua trong `pytest`. Ghi kết quả vào bảng cuối trang; không ghi "đạt" cho mục chưa chạy.

## (a) Cấu hình provider LLM

Làm theo README mục "Bật LLM" (Anthropic hoặc tương thích OpenAI, đủ 8 khóa model và giá). Kiểm tra đã nhận cấu hình:

```bash
uv run python -c "from citeproof.core.settings import Settings; print(Settings().llm_configured)"
```

In `True` mới chạy được (b) và (d). In `False` thì ghi "chưa chạy: chưa cấu hình LLM".

## (b) Test live LLM

```bash
CITEPROOF_LIVE_LLM=1 uv run pytest -m live -q
```

Tốn phí thật (hoặc dùng model cục bộ giá 0).

## (c) Test live GROBID

```bash
docker compose up -d grobid
curl -s localhost:8070/api/isalive        # đợi tới khi in "true" (có thể mất vài phút)
CITEPROOF_LIVE_GROBID=1 uv run pytest -m grobid -q
```

## (d) Một bài thật qua giao diện

1. `docker compose up -d --build`, tạo tài khoản admin (`create-user --admin`), đăng nhập, đồng ý.
2. Tải lên một bài thật (PDF), chờ run xong.
3. Mở "Chi tiết kỹ thuật" (chỉ admin thấy) và đọc chi phí, số lần gọi, độ trễ.
4. Ghi vào bảng dưới; chi phí thật là đầu vào cho T-008 và T-703.

## Bảng ghi kết quả

| Ngày | Kiểm tra | Provider/model | Kết quả (nguyên văn) | Chi phí (USD) | Người chạy |
|---|---|---|---|---|---|
| 27/11/2026 | (c) GROBID live | GROBID 0.8.1 | `1 passed, 1858 deselected in 17.40s` | 0 | Task 16 |
| 27/11/2026 | (b) LLM live | - | chưa chạy: chưa cấu hình LLM | - | - |
| | (d) Bài thật qua UI | | | | |
