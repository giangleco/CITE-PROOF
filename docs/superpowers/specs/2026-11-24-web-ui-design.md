# Thiết kế giao diện web v1 — CiteProof

| Thuộc tính | Giá trị |
|---|---|
| Ngày | 24/11/2026 |
| Trạng thái | Chờ duyệt |
| Nhánh | `feat/web-ui` (tách từ `feat/m1-foundation`) |
| Tài liệu đi kèm | `.stitch/DESIGN.md` (ngôn ngữ thị giác), `.stitch/SITE.md` (sơ đồ trang, lộ trình) |
| Bản thử đã duyệt | `.superpowers/brainstorm/4751-1795454244/content/prototype-v1.html`, Hướng A |
| Nguồn yêu cầu | `04` §4.2–§4.8, `09` §4.1–§4.5, `08` §4.2 và §4.5, `10` (xác thực, tải lên), `07` ADR-005 |

## 1. Mục đích và lý do

Người dùng (chủ dự án, cũng là nghiên cứu viên) muốn có giao diện để **tự thực nghiệm** hệ thống ngay, trước khi các giai đoạn xử lý thật (M2–M5) hoàn tất, và để giao diện đó trở thành giao diện pilot mà không phải làm lại.

Vì vậy giao diện v1 được xây **thật** (đọc và ghi đúng các bảng của `08`), không phải bản mô phỏng. Khi pipeline còn là no-op, dữ liệu để xem đến từ một **bộ dữ liệu mẫu** được nạp vào chính các bảng đó. Khi M2–M5 ghi kết quả thật, các trang hiển thị kết quả thật mà không sửa giao diện.

Bản thử `prototype-v1.html` là nguồn tham chiếu cho bố cục, nội dung chữ và cách thể hiện trạng thái. Người dùng đã xem và đồng ý; mọi quyết định cách xây do Claude đưa ra theo yêu cầu "tùy bạn quyết định".

## 2. Mục tiêu

- G1. Toàn bộ hành trình `04` §4.2 bấm được trên giao diện thật: đăng nhập, đồng ý, tải lên hoặc dán văn bản, xem tiến độ, xem kết quả tài liệu tham khảo, xem đối chiếu nhận định cùng bằng chứng, phản hồi, xuất báo cáo, xóa dữ liệu.
- G2. Thực nghiệm được ngay: một lệnh (hoặc một nút trong môi trường local) tạo bài mẫu có đủ mọi trạng thái của `04` §4.5.
- G3. Giao diện thể hiện đúng các quy tắc diễn đạt của `04` §4.5 và §4.8 (không ngụ ý "bịa đặt"; `LOOKUP_FAILED` không bao giờ trông như "chưa tìm thấy").
- G4. Đạt các yêu cầu bảo mật của `10` cho phần web: băm mật khẩu Argon2id, giới hạn đăng nhập, cookie an toàn, chống CSRF, mỗi người chỉ thấy dữ liệu của mình (FR-61).
- G5. Nghiên cứu viên (vai trò `admin`) thấy cấu hình và số liệu kỹ thuật của từng lần chạy (FR-80, FR-81) mà không làm rối giao diện người dùng thường.

## 3. Ngoài phạm vi

- Logic các giai đoạn S1–S9 (parse, tra cứu, kiểm chứng). Giao diện chỉ tạo lần chạy và đọc kết quả.
- Kiểm chứng lại một phần sau khi người dùng tải nguồn (FR-34, T-505) và kiểm tra khớp tiêu đề file nguồn (FR-33). Tab "Thiếu toàn văn" chỉ liệt kê; nút tải lên hiển thị ở trạng thái chưa hỗ trợ, có ghi chú rõ.
- REST API JSON ngoài `/api/v1/health`. Giao diện gọi thẳng tầng dịch vụ (ADR-005); các view model dùng tên trường của `09` §4.4 để sau này dựng API JSON trên cùng tầng dịch vụ.
- Object storage dạng S3 (T-202 chỉ làm phần local), xuất PDF, đa ngôn ngữ giao diện, Server-Sent Events.
- Đổi mật khẩu, quên mật khẩu, gửi email.
- Các giới hạn `09` §4.5 không đo được ở tầng web: số trang tối đa (kiểm ở S1 khi parse, M2), số nhận định tối đa mỗi lần chạy (S4), giới hạn số yêu cầu mỗi phút (gia cố trước pilot, thêm vào checklist `10` §5).
- Tra cứu lại riêng một tài liệu tham khảo (thuộc T-304). Ở v1, mọi hành động "chạy lại" là kiểm tra lại cả bài (§7.3).

## 4. Điều không được xảy ra (anti-goals)

- Một kết quả bị diễn đạt mạnh hơn dữ liệu: "chưa tìm thấy" tô đỏ, "lỗi tra cứu" hiển thị như "chưa tìm thấy", dùng các từ "bịa đặt", "giả mạo", "không tồn tại", "gian lận".
- Người dùng thấy dữ liệu của người khác, hoặc biết được tài nguyên của người khác tồn tại (trả 404, không trả 403).
- Giao diện bịa số liệu: mọi con số là đếm từ dữ liệu; chỗ chưa có dữ liệu ghi `[chưa có]`, không điền số tròn.
- Bài mẫu bị nhầm với kết quả thật: bài mẫu luôn có nhãn "Bài mẫu" ở danh sách và đầu báo cáo.
- Trạng thái chỉ phân biệt bằng màu.

## 5. Ràng buộc

- Stack hiện có: FastAPI, SQLAlchemy async, Alembic, Pydantic v2, Python 3.12, uv; một container `app` phục vụ cả web. Không thêm Node hay bước build frontend.
- Lược đồ `08` giữ nguyên; chỉ thêm bảng cho xác thực (§7.2).
- Tiếng Việt là ngôn ngữ giao diện (NFR-09). Font phải có bộ ký tự tiếng Việt (đã kiểm tra: Geist, Geist Mono, Literata có).
- Không tải tài nguyên từ bên thứ ba lúc chạy (font, script): tự phục vụ để đặt CSP chặt và không lộ địa chỉ IP người dùng.
- Không log nội dung tài liệu, đoạn nguồn, mật khẩu, token phiên (`12`, `10`).
- Mọi giới hạn `[ĐX]` của `09` §4.5 là cấu hình, có giá trị mặc định tạm.

## 6. Các cách đã cân nhắc

| Cách | Đánh đổi | Kết luận |
|---|---|---|
| **A. Jinja2 + HTMX trong FastAPI hiện có** | Một service, toàn Python, CSS thuần dùng lại token của bản thử, không bước build. Tương tác phức tạp làm bằng thay mảnh HTML; cảm giác ít "ứng dụng một trang" hơn | **Chọn.** Đúng ADR-005 và NFR-10 (một người bảo trì) |
| B. React + Vite + shadcn/ui tách riêng | Mượt hơn, nhiều component sẵn; thêm codebase TypeScript, bước build, cơ chế token; trái ADR-005 | Loại: gấp đôi công bảo trì |
| C. Streamlit | Nhanh nhất để xem dữ liệu; không làm được đăng nhập, đồng ý, tách dữ liệu theo người dùng | Loại: phải làm lại ở M3 |

Trong cách A, phần thực nghiệm dùng **dữ liệu mẫu ghi vào bảng thật** thay vì trang giả: cùng một mã hiển thị phục vụ cả dữ liệu mẫu và dữ liệu thật.

## 7. Thiết kế

### 7.1 Cấu trúc mã

```
src/citeproof/
  storage/            Storage protocol + LocalStorage (T-202, phần local)
  schemas/            View model Pydantic theo 09 §4.4 (ReferenceCheckView, ClaimVerificationView, RunView…)
  services/           Tầng ứng dụng, không phụ thuộc HTTP
    auth.py           đăng ký bằng mã mời, đăng nhập, phiên, giới hạn đăng nhập
    documents.py      tải lên, kiểm tra file, tạo tài liệu và lần chạy, hạn mức
    runs_view.py      tiến độ theo giai đoạn (từ bảng jobs), chi tiết kỹ thuật cho admin
    report.py         tổng quan, tài liệu tham khảo, nhận định, bằng chứng, thiếu toàn văn, xuất Markdown
    feedback.py       ghi phản hồi (FR-70)
    deletion.py       xóa tài liệu, xóa tài khoản theo 08 §4.5
    sample.py         nạp bài mẫu
  web/
    app.py            gắn router web, middleware, static vào create_app hiện có
    deps.py           current_user, require_consent, require_admin, csrf
    routes/           auth.py, documents.py, runs.py, report.py, account.py, dev.py
    templates/        base.html, layout, partials/, pages/ (Jinja2, autoescape bật)
    static/           css/tokens.css, css/app.css, js/app.js, vendor/htmx.min.js, fonts/
  cli.py              python -m citeproof.cli: create-user, create-invite, seed-sample
```

Route web không chứa truy vấn SQL; chúng gọi `services/` và render template với view model. Đây là ranh giới để API JSON sau này dùng lại.

### 7.2 Xác thực và phiên

- **Đăng ký bằng mã mời** (`09` §4.2): bảng mới `invites` (`code_hash`, `created_by`, `expires_at`, `used_by`, `used_at`). Mã chỉ hiện một lần khi tạo bằng CLI; DB lưu SHA-256 của mã. Mã dùng một lần.
- **Mật khẩu**: Argon2id (`argon2-cffi`, tham số mặc định của thư viện), độ dài tối thiểu 10 ký tự. Email chuẩn hóa chữ thường, bỏ khoảng trắng.
- **Phiên phía server**: bảng mới `web_sessions` (`token_hash`, `user_id`, `csrf_token`, `created_at`, `last_seen_at`, `expires_at`). Cookie `cp_session` chứa token ngẫu nhiên 32 byte; DB chỉ lưu hash. Cookie `HttpOnly`, `SameSite=Lax`, `Secure` khi không phải môi trường local. Hạn mặc định 14 ngày, trượt theo hoạt động. Đăng nhập tạo phiên mới (chống cố định phiên); đăng xuất xóa phiên; xóa tài khoản xóa mọi phiên.
- **Giới hạn đăng nhập** (`10`): bảng mới `login_attempts` (`email_norm`, `ip`, `succeeded`, `created_at`). Quá 5 lần sai trong 15 phút theo email hoặc 20 lần theo IP thì từ chối với thông báo chờ. Bản ghi cũ hơn 1 ngày bị dọn khi ghi bản mới.
- **CSRF**: mỗi phiên có `csrf_token`; mọi form POST có trường ẩn, mọi request HTMX gửi header `X-CSRF-Token` (đọc từ thẻ `<meta>`). Form đăng nhập và đăng ký (chưa có phiên) dùng cookie CSRF tạm, kiểu double-submit. Sai token trả 403 với trang lỗi.
- **Đồng ý dữ liệu** (FR-63, `10` §4.3): người dùng chưa đồng ý bị chuyển tới `/consent` trước khi vào bất kỳ trang nào tạo lần chạy. Nội dung nêu đúng điều đã chốt, không hứa điều còn phụ thuộc vào nhà cung cấp chưa chọn (Q-03, ADR-006):
  - Thông tin trong danh mục tài liệu tham khảo (tên bài, tác giả, năm, DOI) được gửi tới Crossref, OpenAlex và Semantic Scholar để tra cứu.
  - Các câu có trích dẫn, đoạn văn xung quanh và đoạn nguồn liên quan được gửi tới nhà cung cấp mô hình ngôn ngữ để đánh giá. Tên nhà cung cấp và liên kết tới chính sách dữ liệu của họ lấy từ cấu hình (`llm_provider_name`, `llm_provider_policy_url`); khi chưa cấu hình, hiển thị `[chưa chốt nhà cung cấp]`. Checklist `10` §5 thêm mục: hai giá trị này phải có trước pilot.
  - Bạn xóa được bài và toàn bộ kết quả bất cứ lúc nào.
  - Bỏ câu "Bài của bạn không dùng để huấn luyện mô hình" của bản thử cho tới khi ADR-006 chốt.
- **Đồng ý lại khi nội dung đổi**: cấu hình `consent_text_updated_at`; người dùng có `consent_llm_at` sớm hơn mốc này phải đồng ý lại. Không cần cột mới.
- **Vai trò**: `admin` thấy thêm chọn biến thể khi tạo lần chạy và phần "Chi tiết kỹ thuật". Tài khoản admin tạo bằng CLI.
- Migration `0005_web_auth` thêm ba bảng trên, cùng quy ước `08` (UUID, `created_at`, index khóa ngoại, CHECK cho enum dạng TEXT).

### 7.3 Tải lên và tạo lần chạy

- **File** (FR-01, T-201): nhận `multipart/form-data`, đọc theo luồng, dừng khi vượt `max_upload_bytes` (mặc định 25 MB). Nhận diện bằng chữ ký file, không tin phần mở rộng hay MIME của trình duyệt: PDF bắt đầu bằng `%PDF-`; DOCX là ZIP có `[Content_Types].xml` và `word/document.xml`. Tên file gốc chỉ để hiển thị (cắt 200 ký tự, bỏ ký tự điều khiển và đường dẫn). Lỗi trả về ngay trên form với mã của `09` §4.1 (`UNSUPPORTED_FILE_TYPE`, `FILE_TOO_LARGE`).
- **Văn bản** (FR-02): hai ô, đoạn văn và danh mục tài liệu tham khảo; mỗi ô có giới hạn ký tự cấu hình được (mặc định 50 000 và 20 000). Lưu thành một file JSON trong storage, `documents.kind = TEXT`.
- **Storage local**: khóa đối tượng dạng `documents/<user_id>/<document_id>/original.<ext>`; thư mục gốc cấu hình bằng `CITEPROOF_STORAGE_DIR`, là volume Docker riêng. Đường dẫn được tạo từ UUID, không bao giờ từ tên file người dùng.
- **Hạn mức** (NFR-08): tối đa `runs_per_day` lần chạy mỗi người dùng mỗi ngày (mặc định 20, không áp cho admin); một tài liệu chỉ có một lần chạy đang hoạt động (`RUN_IN_PROGRESS`).
- Tạo tài liệu và lần chạy trong cùng một giao dịch, gọi `create_run` của M1 với `config = {"variant": "V2"}` (admin chọn được V0, V1, V2). Sau đó chuyển tới trang tiến độ.
- **Kiểm tra lại**: nút ở đầu báo cáo tạo lần chạy mới, đầy đủ, cho cùng tài liệu (tính vào hạn mức; admin chọn lại biến thể). **Không đặt `parent_run_id`**: cột này dành cho kiểm chứng lại một phần (FR-34, T-505) theo `08`. Danh sách tài liệu luôn dẫn tới lần chạy mới nhất; admin thấy danh sách các lần chạy khác của tài liệu trong "Chi tiết kỹ thuật".
- Hành động "chạy lại" gắn với `LOOKUP_FAILED` và lý do `PROCESSING_ERROR` ở v1 là chính nút "Kiểm tra lại" này, có ghi rõ là chạy lại cả bài.
- **Bài mẫu không kiểm tra lại được** (không có file gốc): nút ẩn trên giao diện, và server từ chối với thông báo rõ nếu vẫn nhận được yêu cầu.

### 7.4 Tiến độ

- Trang `/runs/{id}` hiển thị 9 bước với tên tiếng Việt của DESIGN.md §8. Trạng thái từng bước suy ra từ bảng `jobs` (`QUEUED`, `RUNNING`, `DONE`, `FAILED`); bước chưa có job là "chưa bắt đầu".
- Cập nhật bằng HTMX gọi `/runs/{id}/progress` mỗi 2 giây khi lần chạy còn `QUEUED` hoặc `RUNNING`; dừng gọi khi đã kết thúc (phản hồi không còn thuộc tính `hx-trigger`).
- Khi S3 xong: hiện khối "Đã có kết quả tài liệu tham khảo" (NFR-03). Khi lần chạy `FAILED`: hiện bước lỗi, thông báo theo `runs.error` đã làm sạch, và nút "Tiếp tục từ bước N" gọi `resume_run` (NFR-04). Khi xong: nút mở báo cáo.
- Admin thấy thêm "Chi tiết kỹ thuật": cấu hình lần chạy; với từng bước là thời điểm đưa vào hàng đợi, số lần thử và mã lỗi cuối (`jobs.created_at`, `attempts`, `last_error`); thời điểm tạo và kết thúc lần chạy; số lời gọi LLM, token và chi phí cộng từ `llm_calls`; model và phiên bản prompt đã dùng. **Thời lượng từng bước hiển thị `[chưa có]`**: bảng `jobs` chưa lưu lúc bắt đầu và lúc xong. T-209 được ghi thêm yêu cầu lưu hai mốc này; khi có, giao diện chỉ cần đọc thêm.

### 7.5 Báo cáo

Báo cáo gắn với một lần chạy, gồm bốn tab là bốn route riêng (bookmark được, nút quay lại của trình duyệt hoạt động):

- **Tổng quan** (`04` §4.8 mục 1–3, 7–8): thanh tỷ lệ và chú giải đếm theo trạng thái nhận định và trạng thái tài liệu tham khảo, bấm vào chú giải mở tab tương ứng đã lọc; cảnh báo khi xử lý (`documents.parse_warnings`, `runs.warnings`); danh sách "Nên làm tiếp" sinh từ dữ liệu theo quy tắc cố định (nhận định bị gắn cờ, tài liệu lệch thông tin, tài liệu thiếu toàn văn, tài liệu chưa tìm thấy, tài liệu lỗi tra cứu); dòng giới hạn chung.
- **Tài liệu tham khảo** (FR-12–FR-15): mỗi mục giữ nguyên chuỗi gốc, huy hiệu trạng thái, phần giải thích theo trạng thái như bản thử (bảng trường lệch cho `METADATA_MISMATCH`, danh sách nguồn đã tra và cách tự kiểm tra cho `NOT_FOUND_IN_SEARCHED_SOURCES`, nguồn lỗi kèm nút "Kiểm tra lại" cho `LOOKUP_FAILED`), DOI và liên kết nguồn khi có. Lọc theo trạng thái qua tham số `?status=`. Câu ghi chú riêng cho từng trường lệch của bản thử bị bỏ vì không có cột nào chứa nó.
- **Nhận định** (FR-40–FR-45, FR-51, FR-52): bố cục danh sách bên trái, tờ đối chiếu bên phải. Chọn một nhận định thay tờ đối chiếu bằng HTMX (`hx-get`, `hx-push-url`) để URL `?c=<id>` luôn mở lại đúng nhận định; không có JavaScript thì liên kết thường vẫn hoạt động. Lọc theo `?status=`, `?ref=` và "Nên tự kiểm tra". Tờ đối chiếu gồm: câu trong bài với phần nhận định được tô và marker; đoạn bằng chứng nguyên văn, đặt `lang` theo ngôn ngữ nguồn, kèm quan hệ, trang, mục, liên kết nguồn; giải thích; dữ kiện (dựa trên toàn văn hay tóm tắt, giới hạn; admin thấy thêm model, prompt, verifier); nút phản hồi.
- **Thiếu toàn văn** (FR-32): liệt kê tài liệu được trích chưa có văn bản nguồn và số nhận định đang chờ; nút tải lên ở trạng thái chưa hỗ trợ (§3).
- **Xuất báo cáo** (FR-53, phần Markdown): `/runs/{id}/report.md` tải về file Markdown đủ tám phần của `04` §4.8, sinh từ cùng view model.
- Trạng thái rỗng: lần chạy xong mà không có kết quả (pipeline còn no-op) hiển thị lời giải thích ngắn và nút "Thêm bài mẫu" khi ở môi trường local.

### 7.6 Phản hồi

`POST /claim-verifications/{id}/feedback` với `verdict` (`AGREE`, `DISAGREE`, `UNSURE`, theo enum đã có) và ghi chú tùy chọn (tối đa 2 000 ký tự). Mỗi người dùng có một phản hồi cho mỗi kết quả; gửi lại thì cập nhật. Phản hồi HTMX thay đúng khối nút và hiện toast "Đã lưu phản hồi".

### 7.7 Xóa dữ liệu (FR-62, phần giao diện của T-605)

- **Xóa tài liệu**: hộp thoại xác nhận như bản thử. Thứ tự: xóa `feedback` trỏ tới kết quả của tài liệu; đặt `llm_calls.cache_key` về NULL cho các lời gọi thuộc tài liệu; xóa bản ghi DB (cascade theo `08`, kể cả `llm_cache` namespace `doc:` và `source_files` dạng `USER_UPLOAD`); sau khi commit, xóa đối tượng trong storage. Lỗi xóa file được log (không kèm nội dung) và thử lại ở lần xóa sau bằng cách quét khóa mồ côi theo tiền tố người dùng; không chặn người dùng.
- **Xóa tài khoản** (trang `/account`): yêu cầu nhập lại mật khẩu; xóa từng tài liệu như trên, xóa phản hồi còn lại, xóa phiên, xóa mã mời đã tạo, rồi xóa bản ghi `users`. `llm_calls` giữ số liệu ẩn danh theo `08` §4.5.

### 7.8 Bài mẫu

- `services/sample.py` tạo cho một người dùng một tài liệu "Bài mẫu" cùng lần chạy `COMPLETED`, đủ các dòng `reference_entries`, `works`, `reference_checks`, `citations`, `citation_links`, `claims`, `source_files`, `source_chunks`, `claim_verifications`, `evidence`, `jobs` (9 bước `DONE`), với nội dung như bản thử: 6 tài liệu tham khảo phủ 6 trạng thái, 6 nhận định phủ 5 trạng thái và 2 lý do `UNVERIFIABLE`.
- **Bài mẫu không được làm bẩn tri thức dùng chung** (`works`, nguồn `OPEN_ACCESS` là bộ nhớ chung giữa người dùng, `07a` §4.7):
  - Chỉ chạy khi `CITEPROOF_ENV=local`. CLI và nút đều từ chối ở môi trường khác.
  - Nguồn mẫu là `source_files` dạng `USER_UPLOAD`, `owner_user_id` là người nhận bài mẫu, gắn `reference_entry_id` của bài mẫu, `title_match = MATCH`. Theo `08` §4.4, loại này không bao giờ dùng cho người khác, và bị xóa cùng tài liệu theo `08` §4.5. Nhờ vậy nguồn mẫu được ghi `FULL_TEXT` mà không ảnh hưởng ai.
  - `works`: lấy dòng sẵn có theo DOI nếu đã có (kể cả dòng thật); nếu chưa có thì tạo với `metadata_source = "sample"`. Metadata mẫu là metadata thật của các bài được trích. Không bao giờ ghi đè dòng có sẵn. Khi xóa bài mẫu, dòng `works` có `metadata_source = "sample"` không còn được tham chiếu thì bị xóa theo. Task-tracking của T-301 và T-304 ghi thêm: dòng `metadata_source = "sample"` được phép bị kết quả tra cứu thật ghi đè.
- Dữ liệu mẫu tuân thủ mọi CHECK và quy tắc bằng chứng tối thiểu của `08` §4.4; test kiểm tra điều này.
- Đoạn bằng chứng mẫu là câu có thật trong tóm tắt các bài được trích. File nguồn mẫu không có trong storage; trang hiển thị bình thường vì chỉ đọc `source_chunks`.
- Gọi bằng `python -m citeproof.cli seed-sample --email ...`, hoặc nút "Thêm bài mẫu" chỉ hiện khi `CITEPROOF_ENV=local`. Tài liệu mẫu đánh dấu bằng `parse_warnings` chứa mục `{"code": "SAMPLE_DOCUMENT"}` để giao diện gắn nhãn "Bài mẫu" mà không cần cột mới.

### 7.9 Giao diện chung

- Template Jinja2 với autoescape; partial dùng chung cho huy hiệu trạng thái, cờ, thông báo, nút, ô nhập. Bảng nhãn tiếng Việt của mọi trạng thái nằm ở một module Python duy nhất, sinh từ DESIGN.md §8; template không tự viết nhãn.
- HTMX 2 tự phục vụ (bản cố định phiên bản, kiểm tra SHA-256 khi thêm vào repo), cấu hình `allowEval = false`, `includeIndicatorStyles = false`. Mọi tính năng hoạt động khi không có JavaScript, trừ tự cập nhật tiến độ (khi đó có nút "Làm mới").
- Font Geist, Geist Mono, Literata dạng woff2 tự phục vụ, chỉ các bộ latin, latin-ext, vietnamese; kèm giấy phép OFL.
- Header bảo mật trên mọi phản hồi HTML: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`. Không dùng style hay script nội tuyến.
- Trang lỗi 404, 403, 413, 429, 500 theo giọng văn của DESIGN.md; 500 không lộ chi tiết.
- Giao diện sáng mặc định; giao diện tối theo `prefers-color-scheme` với bảng màu ở DESIGN.md §2.
- Truy cập được: WCAG 2.2 AA cho độ tương phản (đã đo, DESIGN.md §2), điều khiển bằng bàn phím, viền focus rõ, `lang="vi"` cho trang và `lang` riêng cho đoạn nguồn, `aria-live` cho toast và khối tiến độ, tôn trọng `prefers-reduced-motion`.

### 7.10 Cấu hình mới

`storage_dir`, `max_upload_bytes`, `max_text_chars`, `max_reference_chars`, `session_ttl_days`, `runs_per_day`, `cookie_secure` (mặc định bật, tắt khi `env=local`), `login_max_failures_per_email`, `login_max_failures_per_ip`, `login_window_minutes`, `consent_text_updated_at`, `llm_provider_name`, `llm_provider_policy_url`. Thêm vào `Settings` và `.env.example`; Docker Compose thêm volume cho storage.

### 7.11 Chế độ thử tiến độ (chỉ local)

Các giai đoạn no-op xong trong vài mili giây và không bao giờ lỗi, nên không ai bấm thử được tiến độ, lỗi và "Tiếp tục từ bước N" (G1). Khi `CITEPROOF_ENV=local`:

- Form "Kiểm tra bài mới" có thêm khối "Chế độ thử" với hai lựa chọn: thời gian mỗi bước (0 đến 5 giây) và bước giả lập lỗi (không có, hoặc S1 đến S9). Giá trị ghi vào `config.demo` của lần chạy, ví dụ `{"variant": "V2", "demo": {"step_delay_s": 1.5, "fail_at": "S5"}}`.
- Worker chạy bằng `python -m citeproof.pipeline` ở môi trường local dùng giai đoạn thử thay cho no-op: đọc `config.demo` của lần chạy, chờ đúng thời gian, và ném `InputError("Lỗi mô phỏng ở bước …")` (không thử lại) ở bước `fail_at`. Ở môi trường khác, worker vẫn dùng no-op như M1.
- Khi bấm "Tiếp tục từ bước N" trên lần chạy có `config.demo.fail_at`, server xóa `fail_at` khỏi cấu hình rồi mới gọi `resume_run`, để lần tiếp theo chạy qua.
- Khối "Chế độ thử" và mọi nhánh mã của nó không tồn tại ngoài local: kiểm tra ở cả route lẫn worker, có test.

### 7.12 Hợp đồng dữ liệu cho M2–M5

Giao diện là nơi đầu tiên đọc các trường JSON mà chưa có mã nào ghi. Để kết quả thật hiện lên mà không sửa giao diện, các giai đoạn sau phải ghi đúng dạng dưới đây. Ghi vào `docs/08-database-design.md` khi làm task tài liệu.

| Trường | Dạng | Ghi chú |
|---|---|---|
| `reference_checks.mismatches` | Danh sách `{"field": str, "in_document": str \| null, "found": str \| null}`, như `09` §4.4 | Giá trị mặc định `{}` của `08` được hiểu là rỗng. `field` thuộc `title`, `authors`, `year`, `venue`, `doi` |
| `verification_runs.warnings` | Danh sách mã chuỗi | Khớp `StageResult.warnings` hiện có; mỗi chuỗi là một mã ở DESIGN.md §8.6 |
| `documents.parse_warnings` | Danh sách `{"code": str, ...chi tiết}` | Ví dụ `{"code": "SCANNED_PAGES", "pages": [41, 42, 43]}`. Mã `SAMPLE_DOCUMENT` là dấu đánh dấu, không hiển thị như cảnh báo |

Mã chưa có trong DESIGN.md §8.6 hiển thị câu chung "Có cảnh báo khi xử lý" cho người dùng, kèm mã gốc cho admin.

### 7.13 Kiểm thử

- Test dịch vụ với DB test hiện có: đăng ký và mã mời, đăng nhập và giới hạn, phiên hết hạn, nhận diện file, hạn mức, tạo lần chạy, tiến độ suy từ jobs, mọi phần báo cáo trên bài mẫu, phản hồi, xóa tài liệu và tài khoản (kiểm tra cả storage), bài mẫu thỏa CHECK.
- Test route bằng `httpx.AsyncClient` trên ứng dụng thật: chuyển hướng khi chưa đăng nhập và chưa đồng ý; CSRF thiếu hoặc sai bị từ chối; truy cập tài nguyên của người khác trả 404; header bảo mật có mặt; phản hồi HTMX trả mảnh HTML đúng; trang hiển thị đúng nhãn trạng thái và không chứa các từ bị cấm (`bịa đặt`, `giả mạo`, `không tồn tại`, `gian lận`).
- Kiểm tra bằng mắt: chụp màn hình bằng Chrome không giao diện các trang chính ở hai độ rộng (1360 px và 390 px) và so với bản thử. Không đưa vào CI.

## 8. Phần để người xây quyết

- Tên lớp CSS, cách chia partial, cấu trúc nội bộ của từng service.
- Câu chữ của thông báo lỗi và trạng thái rỗng, miễn theo giọng văn và bảng thuật ngữ của DESIGN.md.
- Cách tối ưu truy vấn báo cáo (gộp truy vấn, eager load), miễn mỗi trang dùng số truy vấn cố định, không phụ thuộc số dòng.

## 9. Việc tài liệu kèm theo

- Viết `docs/decisions/ADR-005-server-rendered-ui.md` (Accepted), ghi lựa chọn ở §6.
- Cập nhật `docs/task-tracking.md`:
  - T-305 kéo lên làm trước; ghi phần đã làm của T-201, T-202 (local), T-205 (phần nhận), T-601–T-605 (phần giao diện); thêm task cho bài mẫu, CLI và chế độ thử.
  - T-209: lưu thêm thời điểm bắt đầu và kết thúc của từng job.
  - T-301, T-304: dòng `works` có `metadata_source = "sample"` được phép bị ghi đè.
  - T-201 và S1: kiểm số trang khi parse.
  - Thêm task gia cố trước pilot: giới hạn số yêu cầu mỗi phút.
- Cập nhật `docs/08-database-design.md` với hợp đồng dữ liệu §7.12, và `docs/10-security-risk-assessment.md` §5 với hai mục mới (tên và chính sách nhà cung cấp trên màn hình đồng ý; giới hạn số yêu cầu).
- Cập nhật `docs/project-status.md`, `README.md` (cách chạy giao diện, tạo tài khoản, nạp bài mẫu).
