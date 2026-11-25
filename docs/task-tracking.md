# Task Tracking — CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 24/11/2026 |
| Trạng thái | Bản nháp trước (Phase 4) |
| Tài liệu liên quan | `11-development-roadmap.md` |

Trạng thái: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `IN_REVIEW`, `COMPLETED`. Ưu tiên: P0 (bắt buộc cho MVP Pilot), P1 (nên có). Mọi task tuân theo Definition of Done ở `11` §4.4.

---

## M0 — Hoàn tất Phase 1–5

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-001 | Literature review | Citation fidelity, kiểm chứng xuyên ngôn ngữ, dataset có sẵn | P0 | — | NOT_STARTED | Bảng tổng hợp công trình; kết luận G-4 về tính mới |
| T-002 | Dùng thử giải pháp hiện có | scite Reference Check và ít nhất hai công cụ ở `02` §4.3c trên cùng một bài | P0 | — | NOT_STARTED | Bảng so sánh có ngày kiểm tra; quyết định ADR-008 |
| T-003 | Phân tích mẫu bài | Thu thập và thống kê mẫu bài (`02` §4.5.2) | P0 | — | NOT_STARTED | Bảng thống kê `02` §4.6 đầy đủ |
| T-004 | Phỏng vấn người dùng | Theo bộ câu hỏi `02` §4.5.1 | P0 | — | NOT_STARTED | Kết luận cho A-01, A-03, A-09 |
| T-005 | SPK-01 Parse | GROBID và PyMuPDF trên mẫu bài | P0 | T-003 | NOT_STARTED | Kết quả G-5; quyết định ADR-004 |
| T-006 | SPK-02 và SPK-03 | Độ phủ metadata và tỷ lệ toàn văn mở | P0 | T-003 | NOT_STARTED | Kết quả G-2 |
| T-007 | SPK-04 Xuyên ngôn ngữ | Thử trực tiếp và dịch trước | P0 | T-003 | NOT_STARTED | Kết quả G-3; đầu vào ADR-007 |
| T-008 | SPK-05 Chi phí | Đo token và độ trễ trên một bài | P0 | T-005 | NOT_STARTED | Chi phí ước tính mỗi bài |
| T-009 | Gán nhãn thử | Gán nhãn mẫu nhỏ, đo thời gian | P0 | T-003 | NOT_STARTED | Thời gian trung bình mỗi mục; kết quả G-7 |
| T-010 | Chốt tài liệu Phase 1–5 | Trả lời Q-01 đến Q-10; chốt ADR; cập nhật `04`, `07`–`13` | P0 | T-001–T-009 | NOT_STARTED | Quyết định go/no-go ghi vào Decision Log |

## M1 — Nền tảng

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-101 | Repo và tooling | Cấu trúc theo `12` §4.1; Ruff, mypy, pytest, pre-commit | P0 | T-010 | COMPLETED | `make check` chạy sạch trên repo rỗng |
| T-102 | Docker Compose | App, worker, PostgreSQL + pgvector, GROBID | P0 | T-101 | COMPLETED | Một lệnh khởi động toàn bộ; health check đạt |
| T-103 | Cấu hình và logging | Settings theo môi trường; log JSON có `run_id` | P0 | T-101 | COMPLETED | Không có bí mật trong repo; log có correlation ID |
| T-104 | Lược đồ DB | Model SQLAlchemy và migration đầu tiên theo `08` | P0 | T-102 | COMPLETED | Migration chạy lên và xuống được; test constraint đạt |
| T-105 | Job runner | Hàng đợi trên PostgreSQL, retry, backoff, tiếp tục theo giai đoạn | P0 | T-104 | COMPLETED | Job lỗi được thử lại; run tiếp tục từ giai đoạn lỗi |
| T-106 | CI | Lint, type check, test, quét bí mật | P0 | T-101 | COMPLETED | CI chạy trên mọi push |
| T-107 | `LLMClient` | Đầu ra có cấu trúc, cache theo namespace (`llm_cache`, khóa ở `07a` §4.7.2), retry, ghi `llm_calls`, chế độ ghi/phát lại cho test | P0 | T-104 | COMPLETED | Test chạy không cần gọi mạng; mọi lời gọi được ghi log; cùng đầu vào cho cùng khóa cache |
| T-108 | `PromptParts` và `ContextBuilder` | Nạp prompt có phiên bản; phần cố định và phần thay đổi; khối dữ liệu không tin cậy; ánh xạ ID ngắn ↔ UUID (`07a` §4.8) | P0 | T-107 | COMPLETED | ID ngắn gán tất định; prompt render giống hệt qua nhiều lần chạy |
| T-109 | `http_safe` | HTTP client chống SSRF (`07a` §4.9, SR-11) | P0 | T-101 | COMPLETED | Bộ test SSRF ở `13` §A.4 đạt |

## M2 — Nhận và parse tài liệu

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-201 | Upload và kiểm tra file | MIME, chữ ký file, giới hạn (FR-01). Đã có (nhánh `feat/web-ui`): chữ ký PDF/DOCX, giới hạn kích thước. Đã có (nhánh `feat/core-pipeline`): S1 từ chối DOCX có macro (`DOCX_MACRO_NOT_ALLOWED`), DOCX giải nén quá 200 MB (`FILE_TOO_LARGE`), DOCX/PDF hỏng (`UNSUPPORTED_FILE_TYPE`), PDF quá `max_pages` trang (`PAGE_LIMIT_EXCEEDED`), PDF có mật khẩu (`PDF_ENCRYPTED`), tệp không trích được chữ (`NO_TEXT_EXTRACTED`). | P0 | T-104 | COMPLETED | File sai bị từ chối với mã lỗi đúng |
| T-202 | Object storage | Lớp trừu tượng: local và S3-compatible. Đã có backend local (`citeproof.storage`); còn S3-compatible. | P0 | T-102 | IN_PROGRESS | Đổi backend bằng cấu hình |
| T-203 | Parse PDF | GROBID, `pypdfium2` dự phòng (ADR-004; không dùng PyMuPDF vì AGPL). Đã có (nhánh `feat/core-pipeline`): S1 gọi GROBID `processFulltextDocument` (TEI có tọa độ: mục, trang, chú thích hình/bảng, abstract, `listBibl`); GROBID không truy cập được thì thử lại, lần cuối dùng `pypdfium2` (`PDF_TEXT_FALLBACK`); GROBID lỗi xử lý tài liệu thì dự phòng ngay; test bằng TEI đã ghi và marker `grobid`. Còn: đo ngưỡng G-5 trên mẫu thật (SPK-01, T-005). | P0 | T-201, T-209 | IN_PROGRESS | Đạt ngưỡng G-5 trên bộ golden |
| T-204 | Parse DOCX | python-docx. Đã có (nhánh `feat/core-pipeline`): S1 tách đoạn văn theo thứ tự, tiêu đề mục (style `Heading`/`Title` hoặc mức dàn ý), đoạn trong bảng, chú thích; ghi `parsed.json`. S2 tách danh mục sau tiêu đề "Tài liệu tham khảo" cuối cùng (mục có nhãn hoặc mỗi đoạn một mục, gộp đoạn ngắt dòng, đánh số lại sau tiêu đề nhóm); đúng trên các mẫu golden DOCX. | P0 | T-201, T-209 | COMPLETED | Tách được đoạn văn và danh mục tài liệu tham khảo trên bộ golden |
| T-205 | Đầu vào văn bản | FR-02. Đã có phần nhận đoạn văn và danh mục trên giao diện (lưu JSON). Đã có (nhánh `feat/core-pipeline`): S1 tách đoạn theo dòng trống và mỗi dòng danh mục thành một mục (nhãn `[n]`/`n.`); S2 gộp dòng không nhãn vào mục trước khi phần lớn mục có nhãn, bỏ dòng chỉ có nhãn, rồi parse các mục (GROBID, rule, W1). | P0 | T-104, T-209 | COMPLETED | Đoạn văn và danh mục được tách đúng |
| T-206 | Ngôn ngữ và bảng mã | Nhận diện ngôn ngữ; phát hiện bảng mã cũ, PDF scan (FR-03, FR-04). Đã có (nhánh `feat/core-pipeline`): ngôn ngữ theo đoạn và tài liệu (`vi`/`en`/`mixed`), cảnh báo `LEGACY_ENCODING` (TCVN3, VNI) cho DOCX, văn bản và PDF, cảnh báo `SCANNED_PAGES` (trang PDF dưới 25 ký tự, số trang từ 1). | P0 | T-203 | COMPLETED | Cảnh báo đúng trên mẫu kiểm thử |
| T-207 | Tách tài liệu tham khảo | GROBID, rule, LLM dự phòng; phân loại loại tài liệu. Đã có (nhánh `feat/core-pipeline`): S2 tách danh mục từ `references_raw` hoặc khối sau tiêu đề danh mục cuối cùng (tới "Phụ lục"), gộp mục ngắt dòng/ngắt đoạn (cùng bộ tách với đường pypdfium2 của S1), parse bằng GROBID `processCitationList`, rule rồi W1 (`reference.parse`, lô ≤ 10, trường không có trong chuỗi gốc bị bỏ), phân loại loại tài liệu bằng rule; cảnh báo `NO_REFERENCE_SECTION`, `LLM_NOT_CONFIGURED`. Trường đúng trên cả 8 mẫu golden; mẫu bài thật chờ T-208. | P0 | T-203, T-107 | COMPLETED | Trường được tách đúng trên bộ golden |
| T-208 | Bộ golden parsing | Mẫu PDF/DOCX tiếng Việt, tiếng Anh, bảng mã cũ, nhiều kiểu trích dẫn. Đã có (nhánh `feat/core-pipeline`): 10 mẫu tổng hợp trong `tests/fixtures/golden/` (kết quả mong đợi viết tay và đối chiếu bằng mắt, TEI GROBID đã ghi). Còn: mẫu bài thật đã kiểm tay (chờ T-003). | P0 | T-003 | IN_PROGRESS | Bộ test có kết quả mong đợi đã kiểm tra tay |
| T-209 | Gia hạn lease cho job chạy lâu | Thêm cột riêng `heartbeat_at`/`lease_expires_at` (không ghi đè `locked_at` vì đó là token fencing); worker gia hạn lease định kỳ khi giai đoạn đang chạy; xác định job stale dựa vào cột mới; giao lease cho stage. Điều kiện tiên quyết của stage thật đầu tiên (S1). Lưu thêm thời điểm bắt đầu và kết thúc của từng job (giao diện admin đang hiện `[chưa có]` cho thời lượng bước). | P0 | T-105 | COMPLETED | Job chạy lâu hơn `lock_timeout` không bị nhận lại khi worker còn sống; worker dừng thì lease hết hạn và job được nhận lại; mọi ghi kết quả vẫn được rào bằng `locked_at` |

## M3 — Kiểm tra tài liệu tham khảo và Pilot-α

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-301 | Connector metadata | Crossref, OpenAlex, Semantic Scholar theo `MetadataConnector`. Dòng `works` có `metadata_source = "sample"` (bài mẫu) được phép bị kết quả tra cứu thật ghi đè. Đã có (nhánh `feat/core-pipeline`): ba connector (`integrations/metadata/`) tra theo DOI và tìm kiếm, trả `WorkRecord` (DOI chữ thường, arXiv ID không phiên bản, lĩnh vực và bản PDF mở của OpenAlex, abstract từ JATS/chỉ mục đảo); bản ghi thiếu trường bị bỏ mà nguồn vẫn tính là tra thành công; User-Agent chung, email liên hệ tùy chọn; test trên phản hồi thật đã ghi (`tests/fixtures/metadata/`). S3 ghi `works` (tìm theo DOI → OpenAlex ID → Semantic Scholar ID → arXiv ID; dòng `sample` hoặc quá TTL bị ghi đè, dòng còn mới chỉ được lấp cột trống; lĩnh vực OpenAlex vào `works.field`); DOI của bài mẫu lưu dạng chuẩn hóa để được ghi đè. | P0 | T-107 | COMPLETED | Test với phản hồi HTTP đã ghi sẵn |
| T-302 | So khớp và gán trạng thái | Bộ trạng thái `04` §4.5.2; liệt kê trường lệch. Đã có (nhánh `feat/core-pipeline`): S3 tra Crossref rồi OpenAlex theo DOI, không thấy thì tìm Crossref, OpenAlex, Semantic Scholar (dừng ở nguồn đầu tiên khớp); `WEB`/`THESIS`/`OTHER` không DOI → `UNSUPPORTED_TYPE`, thiếu tiêu đề lẫn DOI → `PARSE_FAILED`; so khớp tất định bằng `rapidfuzz` (DOI trùng, tiêu đề ≥ 90, hoặc ≥ 75 cùng họ tác giả đầu và năm lệch ≤ 1; tên Việt họ trước; NFD khớp NFC); trường lệch `title`/`authors`/`year`/`doi` (không báo `venue`); ứng viên sát điểm và DOI trùng mà tiêu đề khác rõ (< 60) gắn cờ; tiêu đề dưới 4 từ cần thêm năm hoặc họ tác giả đầu khớp; cảnh báo `OUT_OF_DOMAIN` theo lĩnh vực OpenAlex. Còn: đo trên tập D5 (T-506). | P0 | T-301, T-207 | IN_PROGRESS | Đúng trạng thái trên tập D5 (`13`) |
| T-303 | Tài liệu web | URL còn hoạt động, bản lưu trữ (FR-15). Đã có (nhánh `feat/core-pipeline`): S3 kiểm tra URL của tài liệu `WEB` qua `http_safe` (2xx/3xx còn, 4xx/5xx không, URL bị chặn hoặc lỗi mạng là chưa rõ); tra archive.org khi bật `web_archive_lookup` (tắt mặc định, QĐ-42). | P1 | T-302 | COMPLETED | Kết quả đúng trên mẫu URL |
| T-304 | Cache, giới hạn tốc độ, ngắt mạch | `metadata_cache` theo hash truy vấn, có TTL; backoff; ngắt mạch theo nguồn; `LOOKUP_FAILED` khi không nguồn nào tra được (`07a` §4.3.5). Dòng `works` có `metadata_source = "sample"` được phép bị ghi đè. Đã có (nhánh `feat/core-pipeline`): `MetadataGateway` với `metadata_cache` (khóa sha256, không lưu truy vấn, TTL 30/7 ngày, không cache lỗi, không TTL cho DB đánh giá), giới hạn tốc độ theo nguồn dùng chung trong tiến trình, thử lại tối đa 2 lần theo `Retry-After` (≤ 10 s), ngắt mạch sau 3 lỗi liên tiếp trong một lần chạy. S3 dùng gateway; `sources_searched` chỉ gồm nguồn tra thành công (tra không gọi API nào không tính), không nguồn nào thành công → `LOOKUP_FAILED`; lần chạy thứ hai cho cùng tài liệu không gọi lại API (test). | P0 | T-301 | COMPLETED | Lần chạy thứ hai không gọi lại API cho cùng tài liệu; `sources_searched` chỉ chứa nguồn thành công |
| T-305 | Giao diện tối thiểu | Đăng nhập, đồng ý, tải lên, xem kết quả tài liệu tham khảo. Làm trước theo yêu cầu (web UI v1, spec `superpowers/specs/2026-11-24-web-ui-design.md`); hiển thị kết quả thật khi T-302 ghi `reference_checks`. | P0 | T-302 | COMPLETED | Người dùng hoàn thành luồng mà không cần hướng dẫn |
| T-307 | Bài mẫu, CLI, chế độ thử | `seed-sample`, `create-user`, `create-invite`; bước giả lập ở local (spec web UI §7.8, §7.11) | P1 | T-305 | COMPLETED | Bài mẫu phủ mọi trạng thái; thử được lỗi và tiếp tục ở trang tiến độ |
| T-306 | Pilot-α | 2–5 người dùng thử phần kiểm tra tài liệu tham khảo | P1 | T-305 | NOT_STARTED | Ghi nhận phản hồi; cập nhật `04` nếu cần |

## M4 — Claim và nguồn

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-401 | Marker và liên kết | Kiểu số và tác giả–năm, tên người Việt (FR-20, FR-21). Đã có (nhánh `feat/core-pipeline`): S4 tách câu và tìm marker bằng luật cho mọi loại đầu vào (QĐ-23): marker số (`[1]`, `[1–3]`, `[1, 4]`, khoảng IEEE `[2]–[4]`, kèm trang như `[1, tr. 25]`, `[1: 25]`, `[1, tr. 25; 3, tr. 7]`, `[1, tr. 25, 3, tr. 7]`: chỉ số tài liệu được liên kết), tác giả–năm trong ngoặc (`;`, `et al.`/`và cs.`/`&`, hậu tố a/b, số trang) và tường thuật (`Nguyễn Văn An (2020)`, họ Việt đứng đầu); ngoặc không phải trích dẫn (`[2019]`, `[n = 30]`, `[x, y]`) bị bỏ. Liên kết số theo nhãn khi ít nhất một nửa số mục có nhãn, ngược lại theo thứ tự; tác giả–năm theo tên đầy đủ rồi họ + năm (gấp dấu, tên đệm không mâu thuẫn); số ngoài khoảng, nhãn trùng hay nhiều ứng viên → link NULL (`REFERENCE_UNRESOLVED`), không đoán; đúng trên bộ golden. `citations`, `citation_links`, `claims` tạo một lần cho mỗi tài liệu (QĐ-13). | P0 | T-207 | COMPLETED | Độ chính xác liên kết đạt trên bộ golden |
| T-402 | Tách claim | Span là chuỗi con của câu (FR-22). Đã có (nhánh `feat/core-pipeline`): một claim cho mỗi marker, span tất định theo QĐ-16 tính trên chính chuỗi câu (đoạn từ marker trước tới marker hiện tại, bỏ dấu câu và ngoặc dở hai đầu; dưới 3 từ thì cả câu bỏ marker đầu/cuối), kèm câu trước/sau và ngôn ngữ của câu; `claims.is_claim` (migration `0009`) cho W2. W2 (`claim.extract`) tinh chỉnh span bằng LLM khi có khóa Anthropic, mỗi đoạn có marker một lời gọi; span không phải chuỗi con của câu bị bỏ và giữ span rule, W2 có thể đánh dấu câu không phải claim (`is_claim = false`); không có khóa thì giữ claim rule và run có cảnh báo `LLM_NOT_CONFIGURED`. | P0 | T-401, T-107 | COMPLETED | Kiểm tra chuỗi con luôn đúng |
| T-403 | Xác định truy cập, lấy toàn văn | arXiv, ACL Anthology, OpenAlex/Unpaywall (FR-30, FR-31). Đã có (nhánh `feat/core-pipeline`): S5 lấy bản mở cho mọi work của lần chạy (theo phạm vi): toàn văn thử lần lượt arXiv (`export.arxiv.org/pdf/<id>`), ACL Anthology (DOI `10.18653/v1/…`, CC BY 4.0), OpenAlex `best_oa_location`, Unpaywall (chỉ khi có `CITEPROOF_CONTACT_EMAIL`), tải qua client chống SSRF riêng (≤ `max_source_bytes`, phải là PDF đầy đủ), dừng ở bản đầu tiên tải được; abstract của work lưu thành nguồn riêng. Nguồn là `source_files` `OPEN_ACCESS` dùng chung (không chủ sở hữu), `access_status` `OPEN_FULL_TEXT`/`OPEN_ABSTRACT` (enum `AccessStatus` + CHECK, migration `0010`), kèm URL và giấy phép; trùng nội dung bị bỏ nhờ index duy nhất (`work_id`, `sha256`). Màn hình đồng ý nêu thêm các nguồn tải bản mở; người đồng ý trước 24/11/2026 đồng ý lại. | P0 | T-301 | COMPLETED | Ghi trạng thái truy cập và giấy phép cho mỗi file |
| T-404 | Tải lên tài liệu được trích | Khớp tiêu đề, cảnh báo (FR-32, FR-33) | P0 | T-403 | NOT_STARTED | File chỉ chủ sở hữu truy cập được |
| T-405 | Chia đoạn và lập chỉ mục | Giữ trang, mục, vị trí; embedding; tsvector. Đã có (nhánh `feat/core-pipeline`): S6 lập chỉ mục các tệp nguồn của lần chạy (bản mở dùng chung của các work trong phạm vi, tệp tải lên của chính người dùng cho tài liệu tham khảo của bài; chọn ở `sources/selection.py`, dùng chung với S7). Parse như S1 (GROBID, pypdfium2 dự phòng ở lượt cuối; PDF quá `max_source_pages` = 200 trang bị bỏ), abstract JSON thành một đoạn mục "Abstract", văn bản `.txt` của bộ đánh giá; JSON đã parse lưu ở `<storage_key>.parsed.json`. Chia đoạn theo mục, 250–400 token ước lượng, chồng lấn một câu; chú thích hình/bảng và abstract là chunk riêng, mục danh mục tài liệu tham khảo không chia đoạn; chunk giữ trang, mục, vị trí ký tự trên văn bản đã parse (NFC), vùng, ngôn ngữ; `tsv` sinh sẵn cho tìm full-text; embedding qua protocol `Embedder` (`NoEmbedder` ở v1, ADR-007). Chunk của một tệp ghi một lần trong một giao dịch và dùng chung giữa các lần chạy; tệp không đọc được không có chunk (S8 → `SOURCE_PARSE_FAILED`). | P0 | T-403 | COMPLETED | Chunk truy ngược được vị trí trong nguồn |
| T-406 | `AgentRunner` và khung công cụ | Vòng lặp agent có giới hạn, công cụ chỉ đọc, cache theo bước, `agent_runs` (`07a` §4.5.1; ADR-010). Làm cùng agent đầu tiên được bật (A1 hoặc A2) | P1 | T-108 | NOT_STARTED | Phát lại cho đúng quỹ đạo cũ; dừng đúng khi hết ngân sách |
| T-407 | Quyết định về A1 | Phân loại các ca `NOT_FOUND` từ SPK-02 và pilot-α: lỗi gõ hoặc dịch tiêu đề, hay nguồn trong nước (`07a` TK-03) | P1 | T-006, T-306 | NOT_STARTED | Quyết định ghi vào Decision Log |
| T-408 | Agent A1 Reference Resolver | `meta_search`, `meta_get`, `submit_resolution`; hậu xử lý tất định (`07a` §4.5.2) | P1 | T-406, T-407, T-302 | NOT_STARTED | Tỷ lệ khớp sai trên D5 gần 0 |

## M5 — Kiểm chứng

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-501 | Truy xuất bằng chứng | Tìm chính xác trong một nguồn; chế độ X1, X2; ghi `retrieval_results`. Đã có (nhánh `feat/core-pipeline`): S7 lexical v1 (ADR-007): tsquery từ claim (bỏ stopword vi/en, tối đa 32 từ, nối OR), `ts_rank_cd` trên `tsv` chỉ trong `source_file_id` đã chọn, top-k mỗi biến thể rồi RRF (k=60); biến thể `original` và `translated` (W3 `claim.translate` khi X2, có LLM, claim không phải tiếng Anh; bản dịch làm rơi số bị bỏ); `score_vector` NULL. Chọn nguồn: tệp tải lên của chính người dùng, rồi bản mở toàn văn của work (của lần chạy hoặc lần chạy cha), rồi abstract. Ghi một lần mỗi cặp (tiếp tục sau lỗi không gọi lại W3); V0, V1 không truy xuất. Số thập phân trong claim (`95,7`, `95.7`) được thêm như một token `95.7` (parser `simple` lập chỉ mục số thập phân thành một token) bên cạnh các token `\w+`. | P0 | T-405 | COMPLETED | Recall@k đo được trên tập dev; kết quả tất định |
| T-502 | Phán quyết | Prompt, schema `Verdict`, kiểm tra trích dẫn nguyên văn | P0 | T-501 | NOT_STARTED | Không có bằng chứng nào lọt qua mà không khớp nguyên văn |
| T-503 | Quy tắc trạng thái | Quy tắc abstract, lý do `UNVERIFIABLE`, cờ "nên tự kiểm tra" | P0 | T-502 | NOT_STARTED | Test cho từng quy tắc |
| T-504 | Biến thể | V0, V1, V2 qua cấu hình; V3 nếu kịp | P0 | T-502 | NOT_STARTED | Chọn biến thể bằng file cấu hình |
| T-505 | Kiểm chứng lại một phần | FR-34 | P1 | T-503 | NOT_STARTED | Chỉ claim liên quan được chạy lại |
| T-506 | Harness đánh giá | Nhập D1–D5 vào DB `citeproof_eval`; chạy biến thể, tính chỉ số (`13` §B); chế độ phát lại và mẫu mới; `pg_dump` sau mỗi đợt | P0 | T-504, T-E02 | NOT_STARTED | Một lệnh cho ra báo cáo chỉ số kèm cấu hình; phát lại cho đúng kết quả cũ |

## M6 — Báo cáo và Pilot-β

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-601 | Báo cáo | Các phần ở `04` §4.8. Phần giao diện và xuất Markdown đã có (web UI v1); chờ dữ liệu thật từ T-503. | P0 | T-503 | IN_PROGRESS | Báo cáo đủ phần trên bài thật |
| T-602 | Xem bằng chứng | Đoạn trích, trang, mục, liên kết (FR-51). Tờ đối chiếu đã có (web UI v1); chờ dữ liệu thật. | P0 | T-601 | IN_PROGRESS | US-06 đạt |
| T-603 | Phản hồi | FR-70. Web UI v1. | P0 | T-601 | COMPLETED | US-09 đạt |
| T-604 | Xuất báo cáo | Markdown/HTML (FR-53). Markdown đã có; HTML in được để sau. | P1 | T-601 | IN_PROGRESS | File xuất mở được, đủ nội dung |
| T-605 | Xóa dữ liệu | FR-62 theo `08` §4.5. Kèm: xóa `feedback` theo mục tiêu và đặt `llm_calls.cache_key` về NULL khi xóa tài liệu (quyết định ở nhánh M1). Web UI v1: xóa tài liệu và tài khoản, kèm dọn storage. | P0 | T-104 | COMPLETED | US-10 đạt; không còn file trong storage |
| T-606 | Triển khai pilot | Theo `14` | P0 | T-601 | NOT_STARTED | Checklist `14` §5 đạt |
| T-607 | Pilot-β | Mời người dùng pilot | P0 | T-606, checklist `10` §5 | NOT_STARTED | Ít nhất một lần chạy hoàn chỉnh từ mỗi người dùng tham gia |
| T-608 | Giới hạn số yêu cầu mỗi phút | Gia cố trước pilot cho toàn bộ web/API (`09` §4.5) | P0 | T-606 | NOT_STARTED | Vượt ngưỡng trả 429 |

## M7 — Thực nghiệm

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-701 | Chạy thực nghiệm chính | V0–V3 × X1/X2 × abstract/toàn văn trên tập test | P0 | T-506, T-E06 | NOT_STARTED | Bảng kết quả kèm khoảng tin cậy |
| T-702 | Ablation và phân tích lỗi | Theo `13` §B.6 | P0 | T-701 | NOT_STARTED | Phân loại lỗi có ví dụ |
| T-703 | Chi phí và độ trễ | Từ `llm_calls` | P0 | T-701 | NOT_STARTED | Bảng trade-off chất lượng–chi phí–độ trễ |
| T-704 | Agent A2 Evidence Investigator | `source_outline`, `source_search`, `source_read`, `submit_verdict`; V4 và `V2+A2` (`07a` §4.5.3) | P1 | T-406, T-503 | NOT_STARTED | Chạy được V4 và `V2+A2` trên tập dev; có chỉ số ở `13` §B.1 |
| T-705 | Quyết định chế độ leo thang | So sánh `V2+A2` với V2/V3 về chất lượng và chi phí; chọn cấu hình mặc định | P1 | T-704, T-701 | NOT_STARTED | Quyết định ghi vào Decision Log; cập nhật `04` §4.5.4 (bỏ hoặc giữ TK-13) |

## M8 — Cải tiến

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-801 | Cải tiến theo pilot | Xử lý phản hồi pilot-β | P0 | T-607 | NOT_STARTED | Mỗi phản hồi quan trọng có quyết định xử lý |
| T-802 | Gia cố | Rà soát lại checklist `10` §5; chọn cấu hình mặc định theo kết quả M7 | P0 | T-701 | NOT_STARTED | Checklist đạt; cấu hình mặc định ghi vào Decision Log |

## M9 — Viết bài và kết thúc

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-901 | Bản thảo bài báo | Theo kênh công bố (Q-05) | P0 | T-702, T-703 | NOT_STARTED | Bản thảo đầy đủ, có phần Limitations |
| T-902 | Báo cáo dự án và tài liệu | Cập nhật toàn bộ `docs/` | P0 | T-802 | NOT_STARTED | Tài liệu khớp với hệ thống thực tế |

## Luồng đánh giá

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-E01 | Annotation guideline v1 | Theo `13` §B.4 | P0 | T-009 | NOT_STARTED | Guideline có định nghĩa nhãn và ví dụ biên |
| T-E02 | Dataset sẵn có | Kiểm tra giấy phép, độ phù hợp; chuyển về định dạng chung | P0 | T-001 | NOT_STARTED | Bảng dataset dùng được và lý do |
| T-E03 | Tập CS–AI tiếng Anh (D2) | Gán nhãn theo guideline | P0 | T-E01 | NOT_STARTED | Đạt kích thước tính ở T-009 |
| T-E04 | Tập tiếng Việt (D3) | D3a dịch từ D2; D3b claim tự nhiên | P0 | T-E03 | NOT_STARTED | Có cả D3a và D3b (D3b có thể nhỏ) |
| T-E05 | Dữ liệu sinh (D4) | Thay đổi có kiểm soát theo `13` §B.2 | P1 | T-E03 | NOT_STARTED | Mỗi loại biến đổi có mẫu đã kiểm tra tay |
| T-E06 | Lượt gán nhãn thứ hai | Gán lại tập con sau ít nhất hai tuần; tính kappa; khóa tập test | P0 | T-E03, T-E04 | NOT_STARTED | Có số đo độ nhất quán; tập test được khóa bằng hash |
| T-E07 | Tập kiểm tra tài liệu tham khảo (D5) | Tài liệu thật (gồm nguồn trong nước) và tài liệu tạo giả có chủ đích | P0 | T-003 | NOT_STARTED | Có nhãn trạng thái mong đợi cho từng mục |

---

## Mẫu mô tả task (Phase 6)

Khi bắt đầu một task, tạo mô tả theo mẫu dưới đây (trong issue hoặc trong file ghi chú của task):

```markdown
### T-xxx: <tên task>

1. **Mục tiêu:** <task giải quyết điều gì, thuộc FR/NFR nào>
2. **Giải thích kỹ thuật:** <cách tiếp cận, lý do chọn>
3. **File tạo mới hoặc chỉnh sửa:**
   - `src/citeproof/...`
   - `tests/...`
4. **Các bước thực hiện:**
   1. ...
   2. ...
5. **Kết quả mong đợi:** <hành vi quan sát được>
6. **Yêu cầu kiểm thử:** <unit / integration / golden; dữ liệu test>
7. **Acceptance criteria:** <lấy từ bảng task, bổ sung nếu cần>
```
