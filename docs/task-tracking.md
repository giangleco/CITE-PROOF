# Task Tracking — CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 23/11/2026 |
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
| T-201 | Upload và kiểm tra file | MIME, chữ ký file, giới hạn (FR-01) | P0 | T-104 | NOT_STARTED | File sai bị từ chối với mã lỗi đúng |
| T-202 | Object storage | Lớp trừu tượng: local và S3-compatible | P0 | T-102 | NOT_STARTED | Đổi backend bằng cấu hình |
| T-203 | Parse PDF | GROBID, PyMuPDF dự phòng | P0 | T-201 | NOT_STARTED | Đạt ngưỡng G-5 trên bộ golden |
| T-204 | Parse DOCX | python-docx | P0 | T-201 | NOT_STARTED | Tách được đoạn văn và danh mục tài liệu tham khảo trên bộ golden |
| T-205 | Đầu vào văn bản | FR-02 | P0 | T-104 | NOT_STARTED | Đoạn văn và danh mục được tách đúng |
| T-206 | Ngôn ngữ và bảng mã | Nhận diện ngôn ngữ; phát hiện bảng mã cũ, PDF scan (FR-03, FR-04) | P0 | T-203 | NOT_STARTED | Cảnh báo đúng trên mẫu kiểm thử |
| T-207 | Tách tài liệu tham khảo | GROBID, rule, LLM dự phòng; phân loại loại tài liệu | P0 | T-203, T-107 | NOT_STARTED | Trường được tách đúng trên bộ golden |
| T-208 | Bộ golden parsing | Mẫu PDF/DOCX tiếng Việt, tiếng Anh, bảng mã cũ, nhiều kiểu trích dẫn | P0 | T-003 | NOT_STARTED | Bộ test có kết quả mong đợi đã kiểm tra tay |

## M3 — Kiểm tra tài liệu tham khảo và Pilot-α

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-301 | Connector metadata | Crossref, OpenAlex, Semantic Scholar theo `MetadataConnector` | P0 | T-107 | NOT_STARTED | Test với phản hồi HTTP đã ghi sẵn |
| T-302 | So khớp và gán trạng thái | Bộ trạng thái `04` §4.5.2; liệt kê trường lệch | P0 | T-301, T-207 | NOT_STARTED | Đúng trạng thái trên tập D5 (`13`) |
| T-303 | Tài liệu web | URL còn hoạt động, bản lưu trữ (FR-15) | P1 | T-302 | NOT_STARTED | Kết quả đúng trên mẫu URL |
| T-304 | Cache, giới hạn tốc độ, ngắt mạch | `metadata_cache` theo hash truy vấn, có TTL; backoff; ngắt mạch theo nguồn; `LOOKUP_FAILED` khi không nguồn nào tra được (`07a` §4.3.5) | P0 | T-301 | NOT_STARTED | Lần chạy thứ hai không gọi lại API cho cùng tài liệu; `sources_searched` chỉ chứa nguồn thành công |
| T-305 | Giao diện tối thiểu | Đăng nhập, đồng ý, tải lên, xem kết quả tài liệu tham khảo | P0 | T-302 | NOT_STARTED | Người dùng hoàn thành luồng mà không cần hướng dẫn |
| T-306 | Pilot-α | 2–5 người dùng thử phần kiểm tra tài liệu tham khảo | P1 | T-305 | NOT_STARTED | Ghi nhận phản hồi; cập nhật `04` nếu cần |

## M4 — Claim và nguồn

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-401 | Marker và liên kết | Kiểu số và tác giả–năm, tên người Việt (FR-20, FR-21) | P0 | T-207 | NOT_STARTED | Độ chính xác liên kết đạt trên bộ golden |
| T-402 | Tách claim | Span là chuỗi con của câu (FR-22) | P0 | T-401, T-107 | NOT_STARTED | Kiểm tra chuỗi con luôn đúng |
| T-403 | Xác định truy cập, lấy toàn văn | arXiv, ACL Anthology, OpenAlex/Unpaywall (FR-30, FR-31) | P0 | T-301 | NOT_STARTED | Ghi trạng thái truy cập và giấy phép cho mỗi file |
| T-404 | Tải lên tài liệu được trích | Khớp tiêu đề, cảnh báo (FR-32, FR-33) | P0 | T-403 | NOT_STARTED | File chỉ chủ sở hữu truy cập được |
| T-405 | Chia đoạn và lập chỉ mục | Giữ trang, mục, vị trí; embedding; tsvector | P0 | T-403 | NOT_STARTED | Chunk truy ngược được vị trí trong nguồn |
| T-406 | `AgentRunner` và khung công cụ | Vòng lặp agent có giới hạn, công cụ chỉ đọc, cache theo bước, `agent_runs` (`07a` §4.5.1; ADR-010). Làm cùng agent đầu tiên được bật (A1 hoặc A2) | P1 | T-108 | NOT_STARTED | Phát lại cho đúng quỹ đạo cũ; dừng đúng khi hết ngân sách |
| T-407 | Quyết định về A1 | Phân loại các ca `NOT_FOUND` từ SPK-02 và pilot-α: lỗi gõ hoặc dịch tiêu đề, hay nguồn trong nước (`07a` TK-03) | P1 | T-006, T-306 | NOT_STARTED | Quyết định ghi vào Decision Log |
| T-408 | Agent A1 Reference Resolver | `meta_search`, `meta_get`, `submit_resolution`; hậu xử lý tất định (`07a` §4.5.2) | P1 | T-406, T-407, T-302 | NOT_STARTED | Tỷ lệ khớp sai trên D5 gần 0 |

## M5 — Kiểm chứng

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-501 | Truy xuất bằng chứng | Hybrid BM25 + vector tìm chính xác trong một nguồn; chế độ X1, X2; ghi `retrieval_results` | P0 | T-405 | NOT_STARTED | Recall@k đo được trên tập dev; kết quả tất định |
| T-502 | Phán quyết | Prompt, schema `Verdict`, kiểm tra trích dẫn nguyên văn | P0 | T-501 | NOT_STARTED | Không có bằng chứng nào lọt qua mà không khớp nguyên văn |
| T-503 | Quy tắc trạng thái | Quy tắc abstract, lý do `UNVERIFIABLE`, cờ "nên tự kiểm tra" | P0 | T-502 | NOT_STARTED | Test cho từng quy tắc |
| T-504 | Biến thể | V0, V1, V2 qua cấu hình; V3 nếu kịp | P0 | T-502 | NOT_STARTED | Chọn biến thể bằng file cấu hình |
| T-505 | Kiểm chứng lại một phần | FR-34 | P1 | T-503 | NOT_STARTED | Chỉ claim liên quan được chạy lại |
| T-506 | Harness đánh giá | Nhập D1–D5 vào DB `citeproof_eval`; chạy biến thể, tính chỉ số (`13` §B); chế độ phát lại và mẫu mới; `pg_dump` sau mỗi đợt | P0 | T-504, T-E02 | NOT_STARTED | Một lệnh cho ra báo cáo chỉ số kèm cấu hình; phát lại cho đúng kết quả cũ |

## M6 — Báo cáo và Pilot-β

| ID | Task | Mô tả | Ưu tiên | Phụ thuộc | Trạng thái | Acceptance criteria |
|---|---|---|---|---|---|---|
| T-601 | Báo cáo | Các phần ở `04` §4.8 | P0 | T-503 | NOT_STARTED | Báo cáo đủ phần trên bài thật |
| T-602 | Xem bằng chứng | Đoạn trích, trang, mục, liên kết (FR-51) | P0 | T-601 | NOT_STARTED | US-06 đạt |
| T-603 | Phản hồi | FR-70 | P0 | T-601 | NOT_STARTED | US-09 đạt |
| T-604 | Xuất báo cáo | Markdown/HTML (FR-53) | P1 | T-601 | NOT_STARTED | File xuất mở được, đủ nội dung |
| T-605 | Xóa dữ liệu | FR-62 theo `08` §4.5. Kèm: xóa `feedback` theo mục tiêu và đặt `llm_calls.cache_key` về NULL khi xóa tài liệu (quyết định ở nhánh M1) | P0 | T-104 | NOT_STARTED | US-10 đạt; không còn file trong storage |
| T-606 | Triển khai pilot | Theo `14` | P0 | T-601 | NOT_STARTED | Checklist `14` §5 đạt |
| T-607 | Pilot-β | Mời người dùng pilot | P0 | T-606, checklist `10` §5 | NOT_STARTED | Ít nhất một lần chạy hoàn chỉnh từ mỗi người dùng tham gia |

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
