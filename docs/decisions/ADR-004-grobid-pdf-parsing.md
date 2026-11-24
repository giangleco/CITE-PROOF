# ADR-004: GROBID cho parse PDF, pypdfium2 dự phòng

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted (24/11/2026). Ngưỡng G-5 vẫn `[ĐX]`; xem lại khi có kết quả SPK-01 trên bài thật |
| Ngày | 2026-11-24 |
| Liên quan | FR-01, FR-03, FR-04, FR-10, FR-11, FR-20, NFR-10, SR-03, `07` §4.2, `10` §4.2, T-203, T-207, T-208, spec `superpowers/specs/2026-11-24-core-pipeline-design.md` (QĐ-03, QĐ-04, QĐ-22) |

## Context

- Bài đầu vào chủ yếu là PDF khoa học và khóa luận tiếng Việt; S4 cần vị trí (trang, mục, ký tự) của từng câu có trích dẫn, S6 cần cấu trúc mục của nguồn để chia đoạn.
- `07` §4.2 đề xuất GROBID kèm PyMuPDF dự phòng. PyMuPDF dùng giấy phép AGPL-3.0, không hợp với một dịch vụ web có thể đóng mã.
- GROBID đã có trong `docker-compose.yml` (`lfoppiano/grobid:0.8.1`, dịch vụ `grobid`, cổng 8070) và tách sẵn danh mục tài liệu tham khảo, tiêu đề mục, tọa độ trang.
- SPK-01 (T-005) chưa chạy vì chưa có mẫu bài thật (T-003).

## Problem

Parse PDF (của bài và của nguồn) bằng công cụ nào để có văn bản kèm cấu trúc và vị trí, giấy phép phù hợp, chạy được khi GROBID gặp sự cố?

## Alternatives

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A. GROBID `processFulltextDocument` (TEI có tọa độ) + `pypdfium2` dự phòng | Có mục, đoạn, câu, trang, danh mục tài liệu tham khảo đã cấu trúc; `pypdfium2` giấy phép Apache-2.0/BSD | Thêm một dịch vụ Java cần nhiều RAM; chất lượng với khóa luận tiếng Việt chưa đo |
| B. Chỉ PyMuPDF | Nhanh, một thư viện | AGPL; không có cấu trúc mục hay danh mục |
| C. Chỉ pdfminer/pdfplumber | Thuần Python, MIT | Chậm; tự dựng cấu trúc mục và danh mục bằng heuristic |
| D. Gửi PDF cho LLM để trích cấu trúc | Hiểu bố cục tốt | Tốn chi phí với bài dài; gửi toàn bộ bản thảo ra ngoài; khó tái lập |

## Decision

Chọn A:

- PDF của bài và của nguồn đi qua GROBID `processFulltextDocument` với `segmentSentences=1`, `teiCoordinates`, `includeRawCitations=1`, không consolidate. TEI được parse bằng `defusedxml`.
- Chuỗi reference thô (DOCX, văn bản, PDF dự phòng) đi qua `processCitationList`; sau đó là rule tất định; W1 chỉ khi có LLM.
- GROBID không truy cập được → `RetryableError` để job runner thử lại; ở lần thử cuối dùng `pypdfium2` và ghi cảnh báo `PDF_TEXT_FALLBACK`. GROBID trả lỗi xử lý cho chính tài liệu → dự phòng ngay.
- `pypdfium2` cũng dùng để đếm trang (giới hạn `max_pages`), phát hiện trang scan và đọc trang đầu của tệp người dùng tải lên.
- DOCX dùng `python-docx`.

## Rationale

- Cấu trúc và tọa độ của GROBID là thứ S4, S6 cần; dựng lại bằng heuristic tốn công hơn nhiều.
- Tránh AGPL mà vẫn có dự phòng chạy được không cần dịch vụ ngoài.
- Không gửi bản thảo ra ngoài chỉ để parse.

## Consequences

- **Tích cực:** một đường parse cho bài và nguồn (`citeproof.parsing`); test chạy bằng TEI đã ghi, không cần GROBID trong CI; GROBID chạy thật qua marker `grobid`.
- **Đánh đổi:** máy pilot cần đủ RAM cho GROBID (giới hạn 4 GB `[ĐX]`); bài parse bằng chế độ dự phòng mất tên mục và chất lượng tách danh mục thấp hơn; kết quả cấp tài liệu không parse lại tự động.
- **Việc tiếp theo:** cập nhật `07` §4.2, §4.11 và T-203 (thay PyMuPDF bằng `pypdfium2`); chạy SPK-01 trên mẫu thật để chốt G-5.
