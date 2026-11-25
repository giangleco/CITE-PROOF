# Hệ thống thiết kế: CiteProof

**Project ID:** chưa có dự án Stitch (thiết kế viết từ bản thử `prototype-v1.html`, Hướng A, duyệt ngày 24/11/2026)

Tài liệu này là nguồn chuẩn cho mọi màn hình của CiteProof. Thiết kế chi tiết về hành vi nằm ở `docs/superpowers/specs/2026-11-24-web-ui-design.md`; sơ đồ trang ở `.stitch/SITE.md`.

## 1. Không khí thị giác

**Ẩn dụ: bàn đối chiếu.** Người dùng là sinh viên sắp nộp khóa luận, thường lo lắng. Việc chính của giao diện là đặt câu họ viết cạnh nguyên văn của nguồn họ trích, rồi nói rõ hai bên khớp tới đâu. Giao diện vì thế bình tĩnh, sáng, chính xác như một bàn làm việc của người biên tập: giấy sáng hơi lạnh, chữ mực đậm, một màu bút xanh để đánh dấu, và không bao giờ to tiếng.

Thang đo theo taste-design, **điều chỉnh có chủ ý** so với mặc định (Creativity 9, Variance 8, Motion 6, Density 5):

| Trục | Mức | Lý do |
|---|---|---|
| Mật độ | 5, cân bằng như ứng dụng dùng hằng ngày | Báo cáo có nhiều mục nhưng mỗi mục cần đọc kỹ |
| Biến hóa bố cục | 3, đối xứng có chủ đích | Người dùng phải quét và so sánh; bố cục lạ làm chậm việc đọc |
| Chuyển động | 2, gần như tĩnh | Đây là công cụ đọc; chuyển động lặp làm phân tâm khỏi bằng chứng |

**Chỗ táo bạo duy nhất là tờ đối chiếu**: hai trang giấy đặt đối diện như sách mở. Trang trái là bài của bạn, chữ không chân. Trang phải là nguồn, chữ có chân trên nền giấy hơi ngả, viền trái mang màu của quan hệ bằng chứng. Mọi thứ còn lại im lặng và kỷ luật để tờ này nổi lên.

**Logo chữ:** `Cite[Proof]`, hai dấu ngoặc vuông mang màu bút xanh, gợi marker trích dẫn `[1]`.

## 2. Bảng màu và vai trò

Chỉ một màu nhấn: **xanh mực bút bi**. Mọi màu trạng thái là màu chức năng, không dùng để trang trí. Tất cả cặp chữ trên nền dưới đây đã đo đạt WCAG 2.2 AA (≥ 4.5:1) ở cỡ chữ 13 px.

### 2.1 Giao diện sáng (mặc định)

| Tên | Mã | Vai trò | Tương phản |
|---|---|---|---|
| **Giấy lạnh** | `#F5F7F6` | Nền trang, hơi xanh xám để không lẫn với giấy kem | — |
| **Mặt giấy** | `#FFFFFF` | Nền panel, bảng, ô nhập | — |
| **Mực đậm** | `#1C2421` | Chữ chính, tiêu đề | 14.7 trên Giấy lạnh |
| **Mực nhạt** | `#4D5853` | Chữ phụ, mô tả | 6.9 |
| **Mực mờ** | `#66706B` | Metadata, nhãn cột, số trang | 4.8 trên Giấy lạnh, 5.1 trên Mặt giấy |
| **Đường kẻ** | `#DCE2DF` | Viền panel, đường chia hàng | — |
| **Đường kẻ đậm** | `#C3CCC8` | Viền nút phụ, ô nhập, viền huy hiệu rỗng | — |
| **Bút xanh** | `#24479A` | Màu nhấn duy nhất: nút chính, liên kết, focus, tab đang chọn, marker trích dẫn | 8.6 trên trắng; chữ trắng trên nền này 8.6 |
| **Bút xanh nhạt** | `#E7ECF7` | Nền mục đang chọn, nền marker, tô phần nhận định | Bút xanh trên nền này 7.3 |
| **Giấy nguồn** | `#FBFBF8` | Nền trang phải của tờ đối chiếu | — |

**Màu trạng thái** (chữ trên nền):

| Ý nghĩa | Chữ | Nền | Tương phản | Dùng cho |
|---|---|---|---|---|
| Khớp | `#28704A` | `#E5F1E9` | 5.2 | `SUPPORTED`, `MATCHED`, bước đã xong |
| Một phần | `#8A6100` | `#F8EDCF` | 4.8 | `PARTIALLY_SUPPORTED`, `METADATA_MISMATCH`, cờ "nên tự kiểm tra", cảnh báo |
| Mâu thuẫn | `#B0252F` | `#F9E3E4` | 5.4 | **Chỉ** `CONTRADICTED`, lỗi nhập liệu, nút xóa, bước lỗi |
| Không hỗ trợ | `#9A4A1C` | `#F6E6DA` | 5.1 | `UNSUPPORTED` |
| Trung tính | `#59636B` | `#ECEFF0` | 5.3 | `UNVERIFIABLE`, `UNSUPPORTED_TYPE`, `PARSE_FAILED`, đang chờ |

### 2.2 Giao diện tối (theo `prefers-color-scheme: dark`)

| Tên | Mã | Ghi chú |
|---|---|---|
| Giấy lạnh | `#16191D` | Không dùng đen tuyền |
| Mặt giấy | `#1E2227` | |
| Mực đậm | `#E4E7EA` | 14.2 |
| Mực nhạt | `#AEB5BC` | 8.5 |
| Mực mờ | `#8C949B` | 5.2 trên Mặt giấy |
| Đường kẻ / đậm | `#2C3238` / `#3A4148` | |
| Bút xanh | `#7FB3AE` | Chuyển sang xanh lam ngọc dịu để đọc được trên nền tối; chữ trên nút chính `#10201E` (7.2) |
| Bút xanh nhạt | `#233331` | |
| Giấy nguồn | `#191D21` | |
| Khớp | `#7CC59A` trên `#1F3229` | 6.7 |
| Một phần | `#E0B65A` trên `#352C17` | 7.2 |
| Mâu thuẫn | `#F08A8F` trên `#3A2224` | 6.1 |
| Không hỗ trợ | `#E59A6B` trên `#382A20` | 6.0 |
| Trung tính | `#A3ACB4` trên `#272C31` | 6.1 |

### 2.3 Quy tắc màu

- **"Chưa tìm thấy trong nguồn đã tra" không bao giờ có màu đỏ hay cam.** Nó là huy hiệu rỗng: nền trong suốt, viền Đường kẻ đậm, chữ Trung tính, biểu tượng kính lúp. Lý do: chưa tìm thấy không phải là sai (`04` §4.5.2).
- **"Chưa tra được, lỗi kết nối" (`LOOKUP_FAILED`) là huy hiệu viền nét đứt**, chữ Trung tính, biểu tượng mũi tên tròn "thử lại", luôn kèm một hành động chạy lại (ở v1 là nút "Kiểm tra lại" cả bài, ghi rõ như vậy). Không được trông giống "chưa tìm thấy".
- Màu Mâu thuẫn chỉ dành cho điều thật sự sai hoặc nguy hiểm.
- Không dùng gradient, không phát sáng, không dùng màu tím.

## 3. Quy tắc chữ

| Vai trò | Font | Dùng cho |
|---|---|---|
| Giao diện | **Geist** 400, 500, 600 | Mọi chữ của hệ thống và **chữ người dùng viết** |
| Nguồn | **Literata** 400, 500, nghiêng 400 (trục optical size bật) | **Chữ lấy từ tài liệu khác**: đoạn bằng chứng, chuỗi tài liệu tham khảo nguyên văn |
| Định danh | **Geist Mono** 400, 500 | DOI, marker `[n]`, mã prompt, số đếm trong chú giải |

**Font mang thông tin, không trang trí:** chữ không chân là lời của bạn hoặc của hệ thống; chữ có chân là lời được trích từ nơi khác. Đây là ngoại lệ có chủ ý với quy tắc "không dùng chữ có chân trong phần mềm" của taste-design.

Cả ba font đều có bộ ký tự tiếng Việt (đã kiểm tra trên Google Fonts); tự phục vụ dạng woff2, chỉ các bộ latin, latin-ext, vietnamese.

**Thang cỡ chữ** (sáng và tối như nhau):

| Mức | Cỡ / dòng | Độ đậm | Ghi chú |
|---|---|---|---|
| Hiển thị | `clamp(28px, 3.4vw, 40px)` / 1.15 | 600 | Chỉ ở trang đăng nhập; chữ khít `-0.02em` |
| H1 | 26 px / 1.2 | 600 | Tiêu đề trang; `-0.02em` |
| H2 | 19 px / 1.3 | 600 | Tiêu đề khối |
| H3 | 15 px / 1.4 | 600 | Tiêu đề nhỏ |
| Thân | 15 px / 1.55 | 400 | Tối đa 68 ký tự mỗi dòng |
| Nhỏ | 13 px / 1.45 | 400–500 | Metadata, huy hiệu, chú thích |
| Câu của bạn | 17 px / 1.6 | 400 | Trang trái tờ đối chiếu |
| Đoạn nguồn | 16.5 px / 1.65, Literata | 400 | Trang phải tờ đối chiếu |

**Cấm:** Inter, font hệ thống mặc định làm font chính, font có chân thông thường (Times New Roman, Georgia, Garamond), chữ IN HOA cho nhãn, nhãn nhỏ phía trên mọi tiêu đề, tô một chữ trong tiêu đề bằng màu khác.

## 4. Kiểu thành phần

- **Nút:** cao tối thiểu 40 px (44 px vùng chạm trên màn hình cảm ứng), góc bo nhẹ 10 px, chữ 500.
  - Nút chính: nền Bút xanh, chữ trắng; mỗi màn hình tối đa một nút chính.
  - Nút phụ: nền Mặt giấy, viền Đường kẻ đậm.
  - Nút ẩn: không viền, chữ Mực nhạt.
  - Nút xóa: viền và chữ màu Mâu thuẫn, không tô nền.
  - Khi nhấn: dịch xuống 1 px. Không phát sáng, không con trỏ tùy biến. Chữ trên nút nói đúng việc xảy ra ("Bắt đầu kiểm tra", "Xóa vĩnh viễn"), không kèm mũi tên.
- **Huy hiệu trạng thái:** hình viên thuốc, chữ 13 px đậm 500, **luôn gồm biểu tượng, chữ và màu**. Bảng biểu tượng ở §8.2.
- **Cờ "Nên tự kiểm tra":** biểu tượng lá cờ và chữ màu Một phần, không có nền, đứng cạnh huy hiệu.
- **Panel:** nền Mặt giấy, viền 1 px Đường kẻ, góc bo 16 px, bóng rất nhẹ ngả màu mực (`0 1px 2px rgba(28,36,33,.06), 0 8px 24px -12px rgba(28,36,33,.12)`). Chỉ dùng panel để gom một nhóm có nghĩa (bảng tài liệu, tờ đối chiếu). **Bên trong panel, các mục chia bằng đường kẻ, không lồng thẻ trong thẻ.** Giao diện tối không có bóng.
- **Bảng và danh sách:** đường kẻ giữa các hàng, cả hàng bấm được khi dẫn tới trang khác, nền Bút xanh nhạt mờ khi rê chuột. Số thứ tự tài liệu tham khảo `[n]` dùng Geist Mono màu Mực mờ.
- **Tab:** chữ Mực nhạt; tab đang chọn chữ Mực đậm 500 với gạch dưới 2 px Bút xanh. Số đếm cạnh tên tab cỡ Nhỏ, màu Mực mờ.
- **Chip lọc:** viên thuốc viền Đường kẻ đậm; khi bật thì nền Mực đậm, chữ Mặt giấy.
- **Ô nhập:** nhãn ở trên, chữ gợi ý ở dưới (Nhỏ, Mực mờ), lỗi ở dưới cùng (Nhỏ, màu Mâu thuẫn, kèm biểu tượng). Viền Đường kẻ đậm, góc bo 10 px, focus là vòng 2 px Bút xanh cách 2 px. Không dùng nhãn nổi.
- **Vùng thả file:** viền nét đứt 1.5 px Đường kẻ đậm, góc bo 16 px; khi đã chọn file thì viền liền Bút xanh và hiện tên file trong một chip Bút xanh nhạt.
- **Bước tiến độ:** danh sách đánh số 1–9 (đây thật sự là một chuỗi tuần tự). Vòng số 28 px.
  - Bước xong: nền Khớp và dấu tích.
  - Bước đang chạy: viền Bút xanh và thanh tiến độ 4 px.
  - Bước lỗi: nền Mâu thuẫn.
  - Bước chưa chạy: viền Đường kẻ đậm, chữ Mực mờ.
- **Thông báo:** khối góc bo 10 px, ba loại: trung tính, cảnh báo (Một phần), lỗi (Mâu thuẫn). Câu đầu nói chuyện gì xảy ra, câu sau nói cách xử lý.
- **Tờ đối chiếu:** đầu tờ gồm huy hiệu, cờ và trang trong bài. Thân tờ là hai cột bằng nhau.
  - Cột trái: câu của bạn. Phần nhận định được tô bằng dải Bút xanh nhạt ở 38% dưới của dòng chữ; marker hiển thị Geist Mono trên nền Bút xanh nhạt.
  - Cột phải: nền Giấy nguồn, đoạn bằng chứng Literata có viền trái 3 px theo quan hệ. Hỗ trợ: Khớp. Hỗ trợ một phần: Một phần. Ngược lại: Mâu thuẫn. Liên quan gần nhất: viền nét đứt Đường kẻ đậm.
  - Dưới thân tờ: khối "Vì sao" kèm bảng dữ kiện, rồi hàng nút phản hồi.
- **Đang tải:** khung xương có đúng kích thước của nội dung sắp hiện, nhấp nháy rất nhẹ; không dùng vòng xoay.
- **Trạng thái rỗng:** một câu nói vì sao rỗng và một nút hành động ("Kiểm tra bài mới", "Thêm bài mẫu"). Không dùng hình minh họa.
- **Hộp thoại:** `dialog` gốc, góc bo 16 px, nền mờ `rgba(20,26,24,.45)`, nút hủy bên trái nút xác nhận.
- **Toast:** nền Mực đậm, chữ Mặt giấy, đáy giữa màn hình, tự tắt sau 2.6 giây, `role="status"`. Chữ trong toast dùng cùng động từ với nút đã bấm ("Đã lưu phản hồi", "Đã xóa bài").

## 5. Nguyên tắc bố cục

- Khung trang rộng tối đa 1280 px, căn giữa; lề hai bên `clamp(16px, 4vw, 40px)`; lề trên `clamp(20px, 4vw, 40px)`. Văn bản căn trái, không căn đều hai bên.
- Thanh trên cùng dính đầu trang, chia 3 cột: logo, điều hướng (2 mục), công cụ (tài khoản).
- Dùng CSS Grid cho bố cục; không dùng phép tính phần trăm.
- Bố cục từng màn hình (chi tiết vị trí xem bản thử):
  - Đăng nhập: hai cột 1.1 : 1, bên trái là lời giới thiệu và một ví dụ đối chiếu thu nhỏ, bên phải là form. Không có phần mở đầu căn giữa.
  - Kiểm tra bài mới: cột chính 1.4, cột phụ 1 giải thích hệ thống sẽ làm gì.
  - Tiến độ: danh sách bước bên trái, cột phụ 340 px chứa các khối hành động.
  - Báo cáo, tab Tổng quan: thống kê bên trái 1.25, "Nên làm tiếp" bên phải 1.
  - Báo cáo, tab Nhận định: danh sách 340 px bên trái, tờ đối chiếu bên phải.
- Khoảng cách theo bậc 4 px: 4, 8, 12, 16, 24, 32, 40. Đệm panel 24 px, khoảng giữa khối 16 px, hàng bảng 14 px.
- **Dưới 900 px** mọi bố cục nhiều cột thu về một cột; tờ đối chiếu xếp nguồn bên dưới bài của bạn; danh sách nhận định không còn cuộn riêng. Điều hướng xuống hàng thứ hai của thanh trên.
- **Dưới 600 px** bảng tài liệu ẩn cột ngày; huy hiệu tài liệu tham khảo xuống dưới chuỗi gốc. Không bao giờ có cuộn ngang trang (dải tab và chip được cuộn ngang trong chính nó).
- Chiều cao toàn màn hình dùng `min-height: 100dvh`.

## 6. Chuyển động và tương tác

- Chuyển động chỉ để trả lời hành động của người dùng: nút nhấn (80 ms), toast hiện ra (250 ms), thanh tiến độ của bước đang chạy (400 ms mỗi lần cập nhật). Đường cong `ease-out`. Chỉ animate `transform` và `opacity` (thanh tiến độ được phép animate `width`).
- **Không có vòng lặp liên tục, không hiện dần từng khối khi tải trang.** Đây là điều chỉnh có chủ ý so với taste-design (vốn yêu cầu micro-interaction lặp vô hạn): người dùng cần nhìn bằng chứng đứng yên.
- Khi HTMX thay một khối, khối mới hiện ngay không hiệu ứng; vị trí cuộn giữ nguyên.
- `prefers-reduced-motion: reduce` tắt mọi chuyển động, kể cả thanh tiến độ.
- Focus bàn phím luôn nhìn thấy (vòng 2 px Bút xanh). Mục trong danh sách nhận định chọn được bằng Enter.

## 7. Điều bị cấm

**Cấm chung (taste-design):** emoji; font Inter; font có chân thông thường; đen tuyền `#000000`; bóng phát sáng; màu nhấn quá bão hòa; chữ gradient; con trỏ chuột tùy biến; phần tử chồng lên nhau; hàng ba thẻ bằng nhau; tên chung chung ("John Doe", "Acme"); số tròn giả (`99.99%`); **số liệu bịa**: mọi con số trên màn hình phải đếm từ dữ liệu, chỗ chưa có ghi `[chưa có]`; khối "số liệu hệ thống" trang trí; định dạng `NHÃN // NĂM`; sáo ngữ quảng cáo ("Nâng tầm", "Liền mạch", "Thế hệ mới"); chữ thừa như "Cuộn để khám phá".

**Cấm riêng của CiteProof:**
- Các từ "bịa đặt", "giả mạo", "không tồn tại", "gian lận", "đạo văn" ở bất cứ đâu trong giao diện và báo cáo.
- Màu đỏ hoặc cam cho "Chưa tìm thấy trong nguồn đã tra".
- Hiển thị `LOOKUP_FAILED` giống `NOT_FOUND_IN_SEARCHED_SOURCES`.
- Trạng thái chỉ phân biệt bằng màu.
- Sửa, dịch hay rút gọn đoạn bằng chứng: luôn nguyên văn, đúng ngôn ngữ gốc.
- Kết luận mạnh hơn bằng chứng, ví dụ gọi `UNVERIFIABLE` là "sai".
- Chữ IN HOA cho nhãn; mũi tên `→` cuối chữ trên nút; nối metadata bằng dấu chấm giữa `·`.
- Bài mẫu không có nhãn "Bài mẫu".

## 8. Thuật ngữ và nhãn

Bảng nhãn này là nguồn duy nhất; mã nguồn sinh nhãn từ đây, template không tự viết.

### 8.1 Các bước xử lý

| Mã | Tên hiển thị | Mô tả ngắn |
|---|---|---|
| S1 | Đọc tài liệu | Nhận diện ngôn ngữ, phát hiện trang ảnh quét |
| S2 | Tách danh mục tài liệu tham khảo | |
| S3 | Tra cứu tài liệu tham khảo | Crossref, OpenAlex, Semantic Scholar |
| S4 | Tìm trích dẫn và nhận định | |
| S5 | Lấy toàn văn nguồn | Nguồn mở |
| S6 | Lập chỉ mục nguồn | Chia đoạn, giữ số trang và mục |
| S7 | Tìm đoạn liên quan | |
| S8 | Đối chiếu nhận định | |
| S9 | Tạo báo cáo | |

### 8.2 Trạng thái nhận định

| Mã | Nhãn | Biểu tượng | Màu |
|---|---|---|---|
| `SUPPORTED` | Được nguồn hỗ trợ | Dấu tích | Khớp |
| `PARTIALLY_SUPPORTED` | Hỗ trợ một phần | Vòng tròn tô nửa | Một phần |
| `CONTRADICTED` | Mâu thuẫn với nguồn | Dấu nhân | Mâu thuẫn |
| `UNSUPPORTED` | Nguồn không nói điều này | Vòng tròn gạch chéo | Không hỗ trợ |
| `UNVERIFIABLE` | Chưa kiểm chứng được | Gạch ngang | Trung tính |

### 8.3 Trạng thái tài liệu tham khảo

| Mã | Nhãn | Biểu tượng | Kiểu |
|---|---|---|---|
| `MATCHED` | Đã xác minh | Dấu tích | Khớp |
| `METADATA_MISMATCH` | Lệch thông tin | Vòng tròn tô nửa | Một phần |
| `NOT_FOUND_IN_SEARCHED_SOURCES` | Chưa tìm thấy trong nguồn đã tra | Kính lúp | Huy hiệu rỗng |
| `UNSUPPORTED_TYPE` | Loại tài liệu chưa hỗ trợ tra cứu | Tờ giấy | Trung tính |
| `PARSE_FAILED` | Chưa tách được thông tin | Gạch ngang | Trung tính |
| `LOOKUP_FAILED` | Chưa tra được, lỗi kết nối | Mũi tên tròn | Viền nét đứt |

### 8.4 Lý do chưa kiểm chứng được

| Mã | Câu hiển thị |
|---|---|
| `REFERENCE_UNRESOLVED` | Chưa xác định được trích dẫn này trỏ tới tài liệu nào trong danh mục. |
| `REFERENCE_NOT_FOUND` | Tài liệu được trích chưa tìm thấy trong các nguồn đã tra. |
| `NO_ACCESSIBLE_TEXT` | Chưa có toàn văn hay tóm tắt của nguồn. Bạn có thể tải PDF lên. |
| `ABSTRACT_ONLY_INSUFFICIENT` | Chỉ có tóm tắt của nguồn, và tóm tắt chưa đủ để kết luận. |
| `SOURCE_PARSE_FAILED` | Chưa đọc được văn bản của nguồn. |
| `EVIDENCE_INCONCLUSIVE` | Có văn bản nguồn nhưng chưa đủ rõ để kết luận. |
| `SOURCE_LANGUAGE_UNSUPPORTED` | Nguồn viết bằng ngôn ngữ chưa hỗ trợ; hiện chỉ hỗ trợ tiếng Việt và tiếng Anh. |
| `NOT_A_CLAIM` | Câu này không chứa nhận định để kiểm chứng. |
| `PROCESSING_ERROR` | Lỗi hệ thống khi xử lý nhận định này. Bạn có thể kiểm tra lại bài. |

### 8.5 Nhãn khác

| Nhóm | Mã → nhãn |
|---|---|
| Quan hệ bằng chứng | `SUPPORTS` Hỗ trợ; `PARTIALLY_SUPPORTS` Hỗ trợ một phần; `CONTRADICTS` Ngược lại; `RELATED` Đoạn liên quan gần nhất |
| Mức truy cập | `FULL_TEXT` Toàn văn; `ABSTRACT` Tóm tắt; `NONE` Không có văn bản nguồn |
| Lần chạy | `QUEUED` Đang chờ; `RUNNING` Đang kiểm tra; `COMPLETED` Đã xong; `COMPLETED_WITH_WARNINGS` Đã xong, có cảnh báo; `FAILED` Tạm dừng do lỗi; `CANCELLED` Đã hủy |
| Loại tài liệu | `ARTICLE` Bài báo; `CONFERENCE` Bài hội nghị; `PREPRINT` Bản tiền ấn phẩm; `BOOK` Sách; `THESIS` Luận văn, luận án; `WEB` Trang web; `OTHER` Loại khác; `UNKNOWN` Chưa rõ loại |
| Nguồn tra cứu | `crossref` Crossref; `openalex` OpenAlex; `semantic_scholar` Semantic Scholar |
| Phản hồi | `AGREE` Đồng ý; `DISAGREE` Không đồng ý; `UNSURE` Không chắc |
| Tài liệu của nhận định chưa xác định | `UNRESOLVED_REF` [chưa xác định] |
| Tài liệu không có trích dẫn | `UNCITED_REFERENCE` Không được trích lần nào trong bài. |
| Khớp tên bài của tệp tải lên | `MATCH` Tên bài trong tệp khớp với tài liệu tham khảo.; `MISMATCH` Tên bài trong tệp có vẻ không khớp với tài liệu tham khảo. Kiểm tra lại trước khi dùng.; `UNKNOWN` Chưa kiểm tra được tên bài trong tệp. |
| Tệp tải lên đã được dùng | `SOURCE_IN_USE` Tệp này đã được dùng trong một lần kiểm tra nên chưa xóa được, để kết quả của lần đó vẫn đầy đủ. |
| Nút tải nguồn | `UPLOAD_BUTTON` Tải lên; `DISCARD_BUTTON` Xóa tệp này |
| Nút kiểm chứng lại một phần | `REVERIFY_BUTTON` Kiểm chứng lại các tài liệu đã tải; `RETRY_LOOKUPS_BUTTON` Tra lại các tài liệu lỗi kết nối |
| Không kiểm chứng lại được | `NO_NEW_UPLOADS` Chưa có tệp nào được tải lên sau lần kiểm tra này. Hãy tải toàn văn của tài liệu cần đối chiếu trước.; `NO_FAILED_LOOKUPS` Lần kiểm tra này không có tài liệu nào bị lỗi kết nối khi tra cứu.; `RUN_NOT_FINISHED` Chỉ kiểm chứng lại được khi lần kiểm tra này đã xong. |

### 8.6 Cảnh báo khi xử lý

| Mã | Câu hiển thị |
|---|---|
| `SCANNED_PAGES` | Trang {pages} có vẻ là ảnh quét; văn bản ở đó có thể đọc chưa chính xác. |
| `LEGACY_ENCODING` | Văn bản có dấu hiệu dùng bảng mã cũ (TCVN3, VNI); một số chữ có thể bị đọc sai. |
| `OUT_OF_DOMAIN` | Bài có vẻ thuộc lĩnh vực khác CS–AI; kết quả có thể kém chính xác hơn. |
| `NO_REFERENCE_SECTION` | Chưa tách được danh mục tài liệu tham khảo. Bạn có thể dán đoạn văn kèm danh mục ở "Kiểm tra bài mới". |
| `LLM_NOT_CONFIGURED` | Chưa cấu hình mô hình ngôn ngữ, nên các nhận định chưa được đối chiếu; phần tra cứu tài liệu tham khảo vẫn chạy bình thường. |
| `CLAIM_LIMIT_REACHED` | Bài có nhiều nhận định hơn giới hạn của một lần kiểm tra; các nhận định vượt giới hạn chưa được đối chiếu. |
| `COST_LIMIT_REACHED` | Lần kiểm tra đã chạm hạn mức chi phí; các nhận định còn lại chưa được đối chiếu. |
| `PDF_TEXT_FALLBACK` | Tệp PDF được đọc bằng chế độ dự phòng; tên mục và vị trí trong bài có thể chưa chính xác. |
| `REFERENCE_PARSE_DEGRADED` | Một số tài liệu tham khảo được tách bằng luật đơn giản vì dịch vụ phân tích tạm thời không khả dụng; thông tin có thể chưa đầy đủ. |
| `SAMPLE_DOCUMENT` | Không hiển thị như cảnh báo; giao diện gắn nhãn "Bài mẫu". |
| Mã khác | Có cảnh báo khi xử lý. (Admin thấy thêm mã gốc.) |

### 8.7 Giọng văn

- Nói với người dùng là "bạn"; câu ngắn, động từ rõ, viết hoa chữ đầu câu.
- Gọi tên theo cách người dùng hiểu: "tài liệu tham khảo", "nhận định", "nguồn", "toàn văn". Không dùng từ kỹ thuật như "claim", "chunk", "job", "pipeline" trên giao diện người dùng thường (admin được thấy mã kỹ thuật trong "Chi tiết kỹ thuật").
- Lỗi nói chuyện gì xảy ra và cách xử lý; không xin lỗi, không mơ hồ.
- Trạng thái rỗng là lời mời hành động.
- Một hành động giữ cùng một tên qua cả luồng: nút "Xóa vĩnh viễn" cho ra toast "Đã xóa bài".
