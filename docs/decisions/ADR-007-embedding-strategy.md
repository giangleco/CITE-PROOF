# ADR-007: Truy xuất v1 không dùng embedding; xuyên ngôn ngữ bằng dịch trước

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted cho v1 (24/11/2026). Xem lại sau SPK-04 (T-007) hoặc khi M7 cho thấy Recall@k thấp |
| Ngày | 2026-11-24 |
| Liên quan | D-06, NFR-06, NFR-10, `07` §4.5 (S6, S7), §4.7 (X1, X2), `07a` TK-19, `08` `source_chunks`, `13` §B.1, §B.6, spec `superpowers/specs/2026-11-24-core-pipeline-design.md` (QĐ-02, QĐ-24, QĐ-25) |

## Context

- `07` đề xuất truy xuất hybrid (BM25 + vector, gộp RRF) và chọn model embedding sau SPK-04. SPK-04 chưa chạy.
- Truy xuất chỉ diễn ra trong một file nguồn (vài trăm chunk), tìm chính xác, để phát lại được (`07a` TK-19).
- `source_chunks` đã có `tsv` (`to_tsvector('simple', text)`, index GIN), cột `embedding VECTOR(1024)` và `embedding_model` để trống.
- Claim tiếng Việt và nguồn tiếng Anh là trường hợp chính (D-06). Không có embedding đa ngôn ngữ thì tìm theo từ khóa chỉ bắt được số, thuật ngữ, từ viết tắt.

## Problem

Ở v1, truy xuất bằng chứng và xử lý xuyên ngôn ngữ thế nào khi chưa có dữ liệu để chọn model embedding?

## Alternatives

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A. Full-text PostgreSQL trong một nguồn + dịch claim trước (X2, W3) khi có LLM; giữ protocol `Embedder` | Không thêm model hay dịch vụ; tất định; dịch được cache | Kém khi không có LLM; không bắt được diễn đạt khác từ |
| B. Model embedding tự chạy (BGE-M3, multilingual-e5) | Xuyên ngôn ngữ trực tiếp (X1 thật) | Thêm phụ thuộc lớn (torch), CPU/RAM; chọn model chưa có số đo |
| C. API embedding của nhà cung cấp | Không vận hành model | Thêm nhà cung cấp và chi phí; gửi thêm nội dung ra ngoài; chọn chưa có số đo |

## Decision

Chọn A cho v1:

- S7 dùng `ts_rank_cd` trên `tsv` với truy vấn OR từ token của claim (bỏ stopword vi/en, tối đa 32 từ), xếp theo điểm rồi `chunk_index`, chỉ trong `source_file_id` của cặp.
- Mặc định `cross_lingual = X2`: claim không phải tiếng Anh được W3 dịch (bỏ bản dịch nếu làm rơi số liệu), hai biến thể truy vấn `original` và `translated` gộp bằng RRF (k = 60). Không có LLM thì chỉ có X1 lexical.
- Nguồn toàn văn không có chunk nào khớp → `UNVERIFIABLE`/`EVIDENCE_INCONCLUSIVE`, không gọi LLM.
- Protocol `Embedder` (`model`, `dim`, `embed(texts)`) và `NoEmbedder` có sẵn; S6 ghi embedding khi embedder trả về vector. `retrieval_results.score_vector` để NULL.

## Rationale

- Chưa có số đo để chọn model; thêm model bây giờ là đoán.
- A giữ đúng yêu cầu tất định và phát lại; X2 đã là một chế độ của thiết kế (`07` §4.7).
- Khi có số đo, chỉ cần một `Embedder` thật và một nhánh vector trong S7; lược đồ không đổi nếu số chiều là 1024.

## Consequences

- **Tích cực:** ít thành phần; chạy trên CPU nhỏ; kết quả lặp lại được.
- **Đánh đổi:** chế độ X1 thực chất là lexical; ablation "chỉ vector / hybrid" của `13` §B.6 chưa chạy được; truy xuất phụ thuộc chất lượng bản dịch khi bài tiếng Việt.
- **Việc tiếp theo:** chạy SPK-04 so X1 lexical, X2 và một model embedding trên tập dev; nếu đổi, tạo ADR mới thay ADR này và cập nhật `07` §4.5, `13` §B.6.
