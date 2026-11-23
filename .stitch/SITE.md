# SITE.md — CiteProof

## 1. Bản sắc cốt lõi

| Mục | Nội dung |
|---|---|
| Tên dự án | CiteProof AI |
| Stitch Project ID | `[Stitch Project ID]` (chưa tạo dự án Stitch) |
| Sứ mệnh | Giúp người viết khóa luận biết tài liệu tham khảo nào sai thông tin hoặc chưa tìm thấy, và nhận định nào có thể không được nguồn hỗ trợ, kèm bằng chứng nguyên văn để tự đối chiếu. |
| Người dùng chính | Sinh viên năm cuối ngành CNTT viết khóa luận tiếng Việt, trích chủ yếu bài tiếng Anh (P1, `02` §4.2) |
| Người dùng phụ | Học viên cao học, nghiên cứu sinh CS–AI (P2) |
| Vai trò nội bộ | Nghiên cứu viên, vai trò `admin`: chọn biến thể kiểm chứng, xem cấu hình và chi phí từng lần chạy |
| Giọng văn | Bình tĩnh, chính xác, không buộc tội, nói thẳng việc cần làm |

## 2. Ngôn ngữ thị giác

| Bậc | Từ khóa | Nghĩa |
|---|---|---|
| Chính | Bàn đối chiếu | Câu của bạn và nguyên văn nguồn đặt cạnh nhau như hai trang sách mở |
| Phụ | Bình tĩnh, đáng tin | Giấy sáng hơi lạnh, chữ mực đậm, một màu bút xanh; màu đỏ chỉ cho điều thật sự sai |
| Thứ ba | Chính xác | Font mang thông tin (không chân là lời của bạn, có chân là lời trích), số liệu chỉ đếm từ dữ liệu |

Chi tiết token, thành phần, quy tắc cấm và bảng nhãn: `.stitch/DESIGN.md`.

## 3. Kiến trúc và cấu trúc file

Dự án không dùng luồng `queue/ → site/public/` mặc định của Stitch: giao diện được render phía server bằng FastAPI + Jinja2 + HTMX (ADR-005), nằm trong package Python.

```
src/citeproof/
  web/templates/        base.html, partials/, pages/
  web/static/css/       tokens.css (biến màu, chữ, khoảng cách theo DESIGN.md), app.css
  web/static/js/        app.js (toast, hộp thoại, CSRF cho HTMX)
  web/static/vendor/    htmx.min.js (cố định phiên bản)
  web/static/fonts/     Geist, Geist Mono, Literata (woff2, bộ vietnamese) + giấy phép OFL
  web/routes/           auth, documents, runs, report, account, dev
  services/             tầng ứng dụng mà route gọi; không chứa HTTP
  schemas/              view model theo 09 §4.4
```

- Bản thử tham chiếu: `.superpowers/brainstorm/4751-1795454244/content/prototype-v1.html` (không commit, chỉ để đối chiếu).
- Nếu sau này dùng Stitch: màn hình sinh ra được chuyển thành template Jinja trong `web/templates/pages/`, dùng lại partial và token, không chép CSS nội tuyến.

**Điều hướng**
- Thanh trên cùng dính đầu trang, gồm:
  - Logo `Cite[Proof]`, bấm vào về `/documents`.
  - Hai mục "Tài liệu của tôi" và "Kiểm tra bài mới".
  - Nút tài khoản (chữ cái đầu tên), mở menu "Tài khoản" và "Đăng xuất".
- Trang đăng nhập, đăng ký và đồng ý ẩn phần điều hướng.
- Báo cáo dùng thanh tab: Tổng quan, Tài liệu tham khảo, Nhận định, Thiếu toàn văn. Mỗi tab là một URL riêng.

## 4. Sơ đồ trang

`[x]` là đã có; `[ ]` là cần xây. Mọi trang trừ đăng nhập và đăng ký yêu cầu đăng nhập; các trang tạo lần chạy yêu cầu đã đồng ý điều khoản dữ liệu.

**Trang**
- [x] Bản thử tham chiếu `prototype-v1.html` (toàn bộ luồng, dữ liệu mẫu, không nối backend)
- [ ] `/login` Đăng nhập
- [ ] `/register` Đăng ký bằng mã mời
- [ ] `/consent` Đồng ý gửi nội dung tới nhà cung cấp mô hình ngôn ngữ
- [ ] `/` chuyển tới `/documents`
- [ ] `/documents` Tài liệu của tôi (lần chạy mới nhất của mỗi bài; nhãn "Bài mẫu"; trạng thái rỗng kèm "Kiểm tra bài mới" và, ở local, "Thêm bài mẫu")
- [ ] `/documents/new` Kiểm tra bài mới (tab tải file PDF/DOCX, tab dán đoạn văn; admin chọn biến thể; ở local có khối "Chế độ thử" đặt thời gian mỗi bước và bước giả lập lỗi)
- [ ] `/runs/{id}` Tiến độ 9 bước (tự cập nhật; tiếp tục sau lỗi; admin thấy chi tiết kỹ thuật)
- [ ] `/runs/{id}/report` Báo cáo, tab Tổng quan
- [ ] `/runs/{id}/references` Tài liệu tham khảo (`?status=`)
- [ ] `/runs/{id}/claims` Nhận định và tờ đối chiếu (`?status=`, `?ref=`, `?flag=1`, `?c=`)
- [ ] `/runs/{id}/missing` Thiếu toàn văn (chỉ liệt kê ở v1)
- [ ] `/runs/{id}/report.md` Tải báo cáo Markdown
- [ ] `/account` Tài khoản (thông tin, xóa tài khoản)
- [ ] Trang lỗi 403, 404, 413, 429, 500

**Hành động (POST)**
- [ ] `/login`, `/register`, `/logout`, `/consent`
- [ ] `/documents` (file), `/documents/text` (đoạn văn)
- [ ] `/documents/{id}/runs` Kiểm tra lại
- [ ] `/documents/{id}/delete` Xóa bài
- [ ] `/runs/{id}/resume` Tiếp tục từ bước lỗi
- [ ] `/claim-verifications/{id}/feedback` Phản hồi
- [ ] `/account/delete` Xóa tài khoản
- [ ] `/dev/sample` Thêm bài mẫu (chỉ khi `CITEPROOF_ENV=local`)

**Mảnh HTMX (GET)**
- [ ] `/runs/{id}/progress` Khối tiến độ
- [ ] `/runs/{id}/claims/{claim_verification_id}` Tờ đối chiếu

## 5. Lộ trình

**Ưu tiên cao**
- Nền giao diện:
  - `base.html`, token CSS theo DESIGN.md, font tự phục vụ, HTMX.
  - Header bảo mật và CSP; trang lỗi.
- Xác thực:
  - Bảng `invites`, `web_sessions`, `login_attempts`; Argon2id; giới hạn đăng nhập; CSRF.
  - CLI `create-user`, `create-invite`.
- Đồng ý dữ liệu (FR-63).
- Tài liệu:
  - Tải lên file có kiểm tra chữ ký và kích thước; dán đoạn văn; storage local.
  - Hạn mức lần chạy; tạo lần chạy.
  - Danh sách tài liệu.
- Tiến độ 9 bước, tự cập nhật bằng HTMX, có tiếp tục sau lỗi.
- Bài mẫu: `seed-sample` và nút ở local; đủ mọi trạng thái; không làm bẩn bảng dùng chung.
- Chế độ thử tiến độ ở local: chậm từng bước, giả lập lỗi, tiếp tục sau lỗi.
- Báo cáo:
  - Bốn tab; tờ đối chiếu; lọc.
  - Nhãn sinh từ một bảng duy nhất.
- Phản hồi trên từng kết quả.
- Xóa tài liệu và xóa tài khoản theo `08` §4.5.

**Ưu tiên vừa**
- Xuất báo cáo Markdown.
- Chi tiết kỹ thuật cho admin; chọn biến thể; kiểm tra lại.
- Giao diện tối theo cài đặt hệ thống.
- Kiểm tra bằng mắt: chụp màn hình các trang chính ở 1360 px và 390 px.
- ADR-005, cập nhật task-tracking, project-status, README.

**Ưu tiên thấp (sau v1)**
- Bật tải nguồn ở tab Thiếu toàn văn khi có T-403 và T-505 (kiểm tra khớp tiêu đề, kiểm chứng lại một phần).
- Server-Sent Events thay cho gọi định kỳ.
- Xuất PDF.
- API JSON `/api/v1/*` trên cùng tầng dịch vụ.
- Chuyển ngôn ngữ giao diện.
- Dựng màn hình trên Stitch từ DESIGN.md nếu cần thử phương án thị giác mới.

## 6. Phạm vi tự do sáng tạo

**Cố định, không được đổi**
- Bảng màu, font, thang chữ, quy tắc màu trạng thái và các điều cấm của DESIGN.md.
- Bảng nhãn ở DESIGN.md §8.
- Tờ đối chiếu hai trang đối diện là trọng tâm của báo cáo.
- Mọi trạng thái gồm biểu tượng, chữ và màu.
- Không thêm thư viện JavaScript hay CSS ngoài HTMX.

**Được tự quyết**
- Cách chia partial và tên lớp CSS.
- Câu chữ của thông báo, gợi ý và trạng thái rỗng, theo giọng văn ở DESIGN.md §8.7.
- Chi tiết khoảng cách trong từng thành phần, miễn theo bậc 4 px.
- Bố cục trang tài khoản và trang lỗi, miễn dùng thành phần sẵn có.
- Cách thể hiện khung xương khi tải, miễn khớp kích thước nội dung.
