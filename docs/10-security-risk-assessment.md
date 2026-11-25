# 10 — Security & Risk Assessment: CiteProof AI (MVP)

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 3 — System Architecture & Technical Design |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | Bản nháp trước |
| Tài liệu liên quan | `03-feasibility-study.md` §4.5, `07-technical-architecture.md`, `09-api-interface-specification.md` |

---

## 1. Overview

Đánh giá bảo mật, quyền riêng tư, bản quyền và rủi ro đạo đức cho MVP Pilot, kèm biện pháp kiểm soát và checklist bắt buộc trước khi mở pilot. Kế thừa các nguyên tắc ở v1.0 §15, điều chỉnh cho quy mô một người vận hành. Tài liệu không thay cho tư vấn pháp lý.

## 2. Objectives

- Bảo vệ bản thảo chưa công bố của người dùng.
- Ngăn lộ dữ liệu giữa người dùng.
- Ngăn tài liệu tải lên điều khiển LLM (prompt injection).
- Tôn trọng bản quyền và điều khoản của nguồn dữ liệu.
- Kiểm soát chi phí trước lạm dụng.

## 3. Scope

Ứng dụng, dữ liệu, tích hợp bên ngoài và môi trường pilot. Ngoài phạm vi: chứng nhận bảo mật chính thức, multi-tenant cho tổ chức.

## 4. Detailed Analysis

### 4.1 Tài sản cần bảo vệ

| Tài sản | Mức nhạy cảm |
|---|---|
| Bản thảo chưa công bố | Cao |
| Tài liệu được trích do người dùng tải lên | Trung bình–Cao (bản quyền) |
| Tài khoản người dùng (email, mật khẩu) | Cao |
| Khóa API (LLM, nguồn dữ liệu) | Cao |
| Kết quả kiểm chứng, phản hồi | Trung bình |
| Dữ liệu đánh giá | Trung bình |

### 4.2 Mối đe dọa và biện pháp kiểm soát

| Nhóm | Mối đe dọa | Biện pháp kiểm soát |
|---|---|---|
| Tải file | File độc hại, file giả định dạng, file nén gây tràn bộ nhớ | Kiểm tra MIME và chữ ký file; giới hạn kích thước và số trang; parse trong container riêng (GROBID, worker); không chạy macro trong DOCX; timeout khi parse |
| Prompt injection | Văn bản trong bài hoặc nguồn chứa chỉ dẫn điều khiển LLM, kể cả xúi agent gọi công cụ | Dữ liệu đặt trong khối có phân tách, đánh dấu không tin cậy; prompt hệ thống yêu cầu bỏ qua chỉ dẫn trong dữ liệu; worker không có công cụ; agent chỉ có công cụ chỉ đọc, phạm vi khóa phía server; validate schema; kiểm tra trích dẫn nguyên văn; quy tắc trạng thái tất định sau LLM (`07` §4.6, `07a` §4.9) |
| SSRF | URL trong bài của người dùng trỏ tới mạng nội bộ (DB, GROBID, metadata cloud) | Mọi truy cập URL lấy từ dữ liệu người dùng đi qua `http_safe`: chỉ `http`/`https`; chặn IP nội bộ, loopback, link-local, metadata cloud, kiểm tra sau phân giải DNS và sau mỗi lần chuyển hướng; giới hạn thời gian và kích thước; không gửi cookie hay header xác thực |
| Truy cập trái phép | Xem dữ liệu người khác qua đổi ID (IDOR) | Mọi truy vấn lọc theo chủ sở hữu; trả 404; test tự động cho từng endpoint |
| Xác thực | Đoán mật khẩu, chiếm phiên | Băm mật khẩu bằng thuật toán chuyên dụng (ví dụ Argon2id); giới hạn số lần đăng nhập; cookie `HttpOnly`, `Secure`, `SameSite`; chống CSRF cho form |
| Rò rỉ qua log | Nội dung bản thảo xuất hiện trong log | Không ghi văn bản tài liệu vào log; chỉ ghi ID và số liệu |
| Bên thứ ba | Nhà cung cấp LLM lưu hoặc dùng dữ liệu | Đọc chính sách dữ liệu của nhà cung cấp (Q-03); chọn chế độ không lưu dữ liệu nếu có; thông báo và xin đồng ý (FR-63) |
| Khóa bí mật | Lộ khóa API | Biến môi trường, không đưa vào repo; quét bí mật trong CI; xoay khóa khi nghi lộ |
| Lạm dụng chi phí | Tải nhiều bài lớn để tiêu tốn LLM | Giới hạn theo `09` §4.5; hạn mức mỗi người dùng; cảnh báo ngân sách (`15`) |
| Chuỗi cung ứng | Thư viện có lỗ hổng | Khóa phiên bản (lock file); công cụ cập nhật phụ thuộc tự động `[ĐX]`; quét lỗ hổng trong CI |
| Bản quyền | Phân phối lại toàn văn không được phép | Chỉ hiển thị đoạn trích ngắn kèm nguồn; không cho tải lại toàn văn; file người dùng tải lên chỉ chủ sở hữu truy cập |
| Đạo đức | Gán nhãn sai làm người dùng bị nghi gian lận (R-06) | Bộ trạng thái và cách diễn đạt ở `04` §4.5; tuyên bố giới hạn trong báo cáo |

### 4.3 Quyền riêng tư và lưu giữ dữ liệu `[ĐX]`

| Chính sách | Đề xuất |
|---|---|
| Thu thập | Tối thiểu: email, tên hiển thị, tài liệu người dùng chủ động tải lên |
| Đồng ý | Màn hình đồng ý trước lần dùng đầu tiên: nêu dữ liệu nào được gửi tới nhà cung cấp LLM và nguồn metadata bên ngoài |
| Lưu giữ | Tài liệu không hoạt động bị xóa tự động sau một khoảng thời gian (chốt con số khi chuẩn bị pilot); người dùng xóa được bất cứ lúc nào |
| Mã hóa | HTTPS khi truyền; mã hóa lúc lưu trữ theo khả năng của dịch vụ DB và storage được chọn |
| Dữ liệu đánh giá | Không dùng bản thảo của người dùng pilot cho tập đánh giá khi chưa có đồng ý riêng |
| Quy định | Tìm hiểu quy định bảo vệ dữ liệu cá nhân hiện hành của Việt Nam trước khi mở pilot |

### 4.4 Sổ rủi ro bảo mật

| ID | Rủi ro | Khả năng | Ảnh hưởng | Biện pháp | Trạng thái |
|---|---|---|---|---|---|
| SR-01 | Lộ bản thảo giữa người dùng | Thấp | Cao | Lọc theo chủ sở hữu, test IDOR | Mở |
| SR-02 | Prompt injection làm sai kết quả | Trung bình | Trung bình | Các kiểm soát ở `07` §4.6; bộ test tài liệu độc hại | Mở |
| SR-03 | File độc hại làm sập worker | Trung bình | Trung bình | Giới hạn, timeout, cách ly | Mở |
| SR-04 | Lộ khóa API | Thấp | Cao | Quản lý bí mật, quét trong CI | Mở |
| SR-05 | Chi phí LLM tăng đột biến | Trung bình | Trung bình | Hạn mức, cảnh báo ngân sách | Mở |
| SR-06 | Vi phạm điều khoản API nguồn dữ liệu | Thấp | Trung bình | Đọc điều khoản (`03` §4.2), cache, giới hạn tốc độ | Mở |
| SR-07 | Phân phối lại nội dung có bản quyền | Thấp | Cao | Chỉ trích đoạn ngắn; file tải lên riêng tư | Mở |
| SR-08 | Nhà cung cấp LLM lưu bản thảo | Phụ thuộc nhà cung cấp | Cao | Q-03; chọn cấu hình phù hợp; thông báo người dùng | Mở |
| SR-09 | Người dùng bị gán nhãn sai | Trung bình | Cao | Bộ trạng thái trung thực; cờ "nên tự kiểm tra" | Mở |
| SR-10 | Mất dữ liệu do sự cố | Thấp | Trung bình | Sao lưu định kỳ, thử khôi phục (`14`) | Mở |
| SR-11 | SSRF qua URL trong tài liệu tham khảo | Trung bình | Cao | `http_safe` (§4.2); test với URL nội bộ | Mở |
| SR-12 | Agent bị dẫn dắt gọi công cụ lặp vô ích hoặc nộp kết quả sai | Trung bình | Trung bình | Ngân sách số bước và token; chỉ nộp được ID có trong phiên; hậu xử lý tất định; theo dõi tỷ lệ hết ngân sách (`15`) | Mở |
| SR-13 | Lộ nội dung người dùng qua cache dùng chung | Thấp | Cao | `llm_cache` theo namespace tài liệu; `metadata_cache` chỉ lưu hash truy vấn (`07a` §4.7.3); test xóa | Mở |

Rủi ro dự án (R-xx) nằm ở `01` §8; sổ này chỉ chứa rủi ro bảo mật.

## 5. Implementation Steps

**Checklist bắt buộc trước khi mở pilot:**

- [ ] Kiểm tra MIME, chữ ký file, giới hạn kích thước và số trang đã hoạt động.
- [ ] Test IDOR cho mọi endpoint P0 đều đạt.
- [ ] Bộ test prompt injection (`13` §A.4) đều đạt, kể cả các ca nhắm vào agent.
- [ ] Bộ test SSRF (`13` §A.4) đều đạt.
- [ ] Không còn nội dung tài liệu trong log (kiểm tra log mẫu).
- [ ] Bí mật nằm ngoài repo; quét bí mật trong CI đạt.
- [ ] Màn hình đồng ý và chức năng xóa dữ liệu hoạt động.
- [ ] Màn hình đồng ý hiển thị tên nhà cung cấp LLM và liên kết chính sách dữ liệu (`CITEPROOF_LLM_PROVIDER_NAME`, `CITEPROOF_LLM_PROVIDER_POLICY_URL`).
- [x] Giới hạn số yêu cầu mỗi phút đã bật cho web và API (T-608). Giới hạn nằm trong tiến trình, chỉ đúng khi chạy một process app. Miễn giới hạn: `/static/`, `/api/v1/health` và `GET /runs/<id>/progress` (poll tiến độ mỗi 2 giây).
- [ ] Đã đọc chính sách dữ liệu của nhà cung cấp LLM và ghi kết luận vào Decision Log.
- [ ] Hạn mức và cảnh báo ngân sách đã cấu hình.
- [ ] Đã thử khôi phục từ bản sao lưu ít nhất một lần.

## 6. Expected Outputs

Sổ rủi ro bảo mật, chính sách dữ liệu cho pilot, checklist trước pilot.

## 7. Dependencies

`07` (kiểm soát LLM), `09` (xác thực, giới hạn), `13` (test bảo mật), `14` (sao lưu, bí mật).

## 8. Risks and Considerations

Bảo mật cho một người vận hành phải ưu tiên kiểm soát tự động (test, CI, cấu hình mặc định an toàn) hơn quy trình thủ công. Nếu một kiểm soát không tự động được, ghi vào checklist và kiểm tra trước mỗi lần triển khai.

## 9. Acceptance Criteria

- [ ] Mọi mối đe dọa ở §4.2 có biện pháp được triển khai hoặc được chấp nhận rủi ro có ghi lý do.
- [ ] Checklist §5 đạt toàn bộ trước pilot.
- [ ] Chính sách ở §4.3 được chốt con số và ghi vào Decision Log.

## 10. References

- CiteProof AI Project Documentation v1.0: §15.
- `03-feasibility-study.md` §4.5.
