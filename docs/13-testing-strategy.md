# 13 — Testing & Evaluation Strategy: CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 5 (chiến lược), Phase 7 (thực hiện) |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | Bản nháp trước |
| Tài liệu liên quan | `04-prd.md`, `07-technical-architecture.md`, `01-idea-analysis.md` §4.5.3 |

---

## 1. Overview

Tài liệu gồm hai phần:

- **Phần A: Kiểm thử phần mềm.** Đảm bảo hệ thống chạy đúng như thiết kế.
- **Phần B: Đánh giá khoa học.** Đo chất lượng kiểm chứng; phục vụ cả sản phẩm lẫn bài báo, theo nguyên tắc "một bộ đánh giá, hai mục đích" (D-01).

Phần B cũng là chiến lược đánh giá trong điều kiện chỉ có một người gán nhãn (D-10).

## 2. Objectives

- Bắt lỗi sớm bằng test tự động, không phụ thuộc mạng hay LLM thật.
- Có các chỉ số chất lượng tái lập được cho từng biến thể.
- Có quy trình gán nhãn minh bạch dù chỉ có một người.

## 3. Scope

Mọi module MVP; tập dữ liệu đánh giá D1–D5; mẫu báo cáo cho Phase 7.

## 4. Detailed Analysis

## Phần A — Kiểm thử phần mềm

### A.1 Các cấp kiểm thử

| Cấp | Đối tượng | Công cụ | Chạy trong CI |
|---|---|---|---|
| Unit | Logic thuần: nhận diện marker, so khớp metadata, quy tắc trạng thái, kiểm tra trích dẫn nguyên văn, chuẩn hóa DOI | pytest | Có |
| Integration | DB, job runner, GROBID, connector với phản hồi HTTP đã ghi | pytest, service container | Có (GROBID có thể tách lệnh riêng) |
| Pipeline | S1–S9 với LLM giả (tất định) và phản hồi LLM đã ghi | pytest | Có |
| API | Endpoint, mã lỗi, phân quyền | FastAPI TestClient / httpx | Có |
| Golden | Parse tài liệu mẫu và so với kết quả mong đợi đã kiểm tra tay | pytest | Có |
| E2E smoke | Tải bài mẫu → báo cáo | Script | Trước mỗi lần triển khai |
| Live | Gọi LLM thật và API ngoài thật | Lệnh riêng | Không; chạy thủ công |
| Bảo mật | §A.4 | pytest | Có |
| Hiệu năng | Bài lớn nhất cho phép; timeout | Script | Trước pilot |

### A.2 Dữ liệu kiểm thử (fixtures)

- PDF và DOCX tiếng Việt, tiếng Anh, có bảng mã cũ, có trang scan.
- Chuỗi tài liệu tham khảo theo nhiều kiểu: APA, IEEE, kiểu tự do thường gặp trong khóa luận Việt Nam, tên người Việt.
- Phản hồi HTTP đã ghi của Crossref, OpenAlex, Semantic Scholar.
- Phản hồi LLM đã ghi qua chế độ ghi/phát lại của `LLMClient` (T-107).
- Tài liệu chứa prompt injection (§A.4).

Không dùng tài liệu của người dùng pilot làm fixture.

### A.3 Test bắt buộc cho các kiểm soát cốt lõi

| Kiểm soát | Test |
|---|---|
| Kiểm tra trích dẫn nguyên văn (`07` §4.6) | Bằng chứng không khớp bị loại; không còn bằng chứng thì kết quả bị hạ về `UNVERIFIABLE` |
| Quy tắc abstract (`04` §4.5.1) | `UNSUPPORTED` từ abstract bị chuyển thành `UNVERIFIABLE` với lý do `ABSTRACT_ONLY_INSUFFICIENT` |
| Lý do `UNVERIFIABLE` | Không thể lưu `UNVERIFIABLE` thiếu lý do |
| `NOT_FOUND_IN_SEARCHED_SOURCES` | Luôn có danh sách nguồn đã tra; văn bản hiển thị không chứa từ bị cấm |
| Span claim | Luôn là chuỗi con của câu gốc |
| Tiếp tục khi lỗi | Lần chạy lỗi ở S5 tiếp tục từ S5, không chạy lại S1–S4 |
| Xóa dữ liệu | Sau khi xóa, không còn bản ghi hay file liên quan |

### A.4 Test bảo mật

- IDOR: người dùng A không đọc, sửa hay xóa được tài nguyên của người dùng B qua mọi endpoint P0.
- Upload: file giả định dạng, file quá lớn, DOCX có macro, file nén bất thường.
- Prompt injection: tài liệu và nguồn chứa câu yêu cầu LLM bỏ qua hướng dẫn hoặc luôn trả `SUPPORTED`. Kết quả không được thay đổi theo chỉ dẫn đó; mọi bằng chứng vẫn phải khớp nguyên văn.
- Log: chạy một lần đầy đủ, kiểm tra log không chứa nội dung tài liệu.

### A.5 Mục tiêu bao phủ `[ĐX]`

Không đặt một con số chung. Mọi hàm trong `verification`, `references` (so khớp, trạng thái) và `pipeline` (tiếp tục khi lỗi) phải có test cho nhánh chính và nhánh lỗi.

### A.6 Mẫu cho Phase 7

**Báo cáo kiểm thử**

```markdown
## Test Report — <milestone> — <ngày>
- Commit: <hash>
- Phạm vi: <module, tính năng>
- Kết quả: <số test đạt / tổng>; <E2E smoke: đạt/không>
- Lỗi phát hiện: <liên kết bug report>
- Rủi ro còn lại: <...>
- Kết luận: <đạt / không đạt tiêu chí milestone>
```

**Báo cáo lỗi**

```markdown
## BUG-xxx: <tiêu đề>
- Mức độ: Nghiêm trọng / Cao / Trung bình / Thấp
- Môi trường: <local / pilot>, commit <hash>
- Các bước tái hiện: 1. ... 2. ...
- Kết quả mong đợi / thực tế: ...
- run_id liên quan (không đính kèm nội dung tài liệu): ...
- Trạng thái: NOT_STARTED / IN_PROGRESS / COMPLETED
```

**Đánh giá chất lượng (cuối Phase 7):** đối chiếu từng FR/NFR P0 với kết quả test; ghi các điểm chưa đạt và quyết định xử lý.

## Phần B — Đánh giá khoa học

### B.1 Tác vụ và chỉ số

| Tác vụ | Chỉ số | Tập dữ liệu |
|---|---|---|
| Parse tài liệu tham khảo | F1 theo từng trường | Bộ golden (T-208) |
| Trạng thái tài liệu tham khảo | Accuracy; **tỷ lệ tài liệu thật bị báo chưa tìm thấy**; tỷ lệ tài liệu tạo giả được phát hiện | D5 |
| Liên kết marker với tài liệu tham khảo | Accuracy | Bộ golden |
| Truy xuất bằng chứng | Recall@k, MRR | D2, D3 (có nhãn đoạn bằng chứng) |
| Phán quyết claim | Macro-F1, F1 từng lớp, ma trận nhầm lẫn | D1–D4 |
| Khoảng cách xuyên ngôn ngữ | Chênh lệch Macro-F1 giữa D3a và D2 trên cùng claim | D2, D3a |
| Ảnh hưởng mức truy cập | Chênh lệch giữa abstract và toàn văn; tỷ lệ `UNVERIFIABLE` | D2 |
| Chất lượng bằng chứng | Tỷ lệ bằng chứng bị loại vì không khớp nguyên văn | Tất cả |
| Hệ thống | Chi phí mỗi claim và mỗi bài; độ trễ; tỷ lệ lỗi | Tất cả |

Calibration (Brier, ECE) hoãn khỏi MVP theo C-1.

### B.2 Tập dữ liệu

| Mã | Nội dung | Nguồn | Gán nhãn |
|---|---|---|---|
| D1 | Dataset tiếng Anh sẵn có (ví dụ SCitance, dataset của CiteAudit, SemanticCite, CoVeGAT) | Công bố công khai; kiểm tra giấy phép ở T-E02 | Có sẵn; ánh xạ nhãn về bộ trạng thái của dự án |
| D2 | Tập CS–AI tiếng Anh: câu trích dẫn trong bài arXiv/ACL kèm toàn văn bài được trích | Tự xây | Bạn gán nhãn |
| D3a | Claim của D2 dịch sang tiếng Việt, giữ nhãn | Tự xây | Kế thừa nhãn D2; kiểm tra bản dịch |
| D3b | Claim tiếng Việt tự nhiên từ bài và khóa luận công khai | Tự xây | Bạn gán nhãn |
| D4 | Dữ liệu sinh: biến đổi claim đã xác nhận `SUPPORTED` | Tự sinh | Nhãn theo cách tạo; kiểm tra tay một mẫu |
| D5 | Tài liệu tham khảo thật (gồm nguồn trong nước) và tài liệu tạo giả có chủ đích | Tự xây | Nhãn trạng thái mong đợi |

**Các phép biến đổi cho D4:** đổi số liệu; khái quát hóa quá mức (bỏ điều kiện, mở rộng phạm vi); đảo chiều kết luận; hoán đổi tài liệu được trích sang một bài không liên quan. Chỉ áp dụng lên claim đã được bạn xác nhận là `SUPPORTED`, vì câu trích dẫn gốc chưa chắc đã được hỗ trợ.

**Chia tập:** mỗi tập có phần dev và phần test. Tập test được khóa bằng hash sau T-E06. Mọi tinh chỉnh prompt và tham số chỉ dùng tập dev.

**Kích thước:** tính ở T-009 từ thời gian gán nhãn đo thực tế, không đặt trước.

### B.3 Định dạng dữ liệu

Mỗi mục là một dòng JSONL:

```json
{
  "id": "d2-000123",
  "claim_text": "...",
  "claim_language": "en",
  "citing_context": "...",
  "cited_work": {"doi": "...", "arxiv_id": "...", "title": "..."},
  "source_content_level": "FULL_TEXT",
  "label": "PARTIALLY_SUPPORTED",
  "evidence_spans": [{"page": 6, "text": "..."}],
  "rationale": "...",
  "annotator_pass": 1,
  "annotated_at": "2026-12-01",
  "split": "dev",
  "origin": "D2"
}
```

### B.4 Annotation guideline (dàn ý)

1. Mục đích và phạm vi: chỉ xét chính tài liệu được trích.
2. Định nghĩa năm nhãn, mỗi nhãn kèm ví dụ rõ ràng và ví dụ biên.
3. Quy tắc quyết định cho trường hợp khó: claim mạnh hơn nguồn; thiếu điều kiện; khác số liệu; kết quả nằm trong bảng hoặc hình; claim trích nhiều nguồn cùng lúc.
4. Yêu cầu bắt buộc: mỗi nhãn khác `UNVERIFIABLE` có ít nhất một đoạn bằng chứng và một câu lý do.
5. Cờ "không chắc": dùng khi do dự giữa hai nhãn; ghi hai nhãn đang cân nhắc.
6. Ghi thời gian cho mỗi mục.
7. Nhật ký thay đổi guideline: mỗi lần sửa định nghĩa ghi lại, kèm các mục bị ảnh hưởng.

### B.5 Độ tin cậy của nhãn khi chỉ có một người

| Biện pháp | Chi tiết |
|---|---|
| Lượt gán nhãn thứ hai | Gán lại một tập con sau ít nhất hai tuần, không xem nhãn cũ; tính Cohen's kappa (intra-annotator agreement) |
| Xử lý bất đồng | Mục có nhãn khác nhau giữa hai lượt được xem lại theo guideline; ghi quyết định cuối và lý do |
| Dataset sẵn có | Dùng D1 để kiểm tra chéo: so cách bạn hiểu nhãn với nhãn có sẵn trên một mẫu nhỏ |
| LLM hỗ trợ | Nếu dùng LLM đề xuất nhãn: không dùng cùng họ model với hệ thống được đánh giá; báo cáo rõ trong bài |
| Minh bạch | Công bố guideline, quy trình và số đo độ nhất quán trong phần Limitations |

Nếu sau này có người hỗ trợ, ưu tiên nhờ họ gán nhãn độc lập một tập con để đo inter-annotator agreement.

### B.6 Baseline, biến thể và ablation

**So sánh chính** (cùng dữ liệu, cùng điều kiện, cùng quy trình, theo v1.0 §16.3):

- V0 (closed-book), V1 (abstract-only), V2 (RAG toàn văn), V3 (pipeline nhiều bước); V4 nếu làm.
- Mỗi biến thể chạy với X1 và X2 trên D3.

**Ablation:**

- Bỏ kiểm tra trích dẫn nguyên văn.
- Bỏ quy tắc trạng thái tất định.
- Chỉ BM25 / chỉ vector / hybrid.
- Thay đổi k trong top-k.

**Đối chiếu bên ngoài:** nếu công trình liên quan (ví dụ CiteAudit) công bố hệ thống hoặc kết quả trên dataset dùng được, so sánh trên phần dữ liệu chung.

### B.7 Tái lập và báo cáo kết quả

- Mỗi lần chạy đánh giá lưu: commit hash, file cấu hình, hash tập dữ liệu, model và phiên bản prompt, ngày chạy.
- Cache phản hồi LLM để chạy lại không tốn chi phí và cho cùng kết quả.
- Báo cáo khoảng tin cậy bằng bootstrap `[ĐX]`, vì tập test nhỏ.
- Kết quả lưu trong `benchmark/results/<ngày>-<cấu hình>/`.

## 5. Implementation Steps

- [ ] Viết guideline v1 (T-E01) sau khi gán nhãn thử (T-009).
- [ ] Kiểm tra giấy phép và chuyển đổi D1 (T-E02).
- [ ] Xây D2–D5 theo roadmap luồng đánh giá (`11`).
- [ ] Viết harness đánh giá (T-506).
- [ ] Thiết lập fixture và test bắt buộc ở §A.3, §A.4 từ M1.

## 6. Expected Outputs

Bộ test tự động; tập dữ liệu D1–D5 có tài liệu mô tả; guideline; harness đánh giá; kết quả thực nghiệm tái lập được.

## 7. Dependencies

`04` (trạng thái), `07` (biến thể, kiểm soát), `11` (thời gian), T-009 (kích thước tập).

## 8. Risks and Considerations

| Rủi ro | Giảm thiểu |
|---|---|
| Rò rỉ tập test vào quá trình tinh chỉnh | Khóa bằng hash; chỉ chạy test ở các mốc định trước |
| Nhãn D1 không khớp bộ trạng thái của dự án | Ghi rõ cách ánh xạ; báo cáo riêng kết quả trên D1 |
| Dữ liệu sinh quá dễ | Báo cáo riêng D4; không dùng D4 làm kết quả chính |
| Bản dịch D3a sai nghĩa | Kiểm tra tay toàn bộ bản dịch hoặc một mẫu lớn |

## 9. Acceptance Criteria

- [ ] Các test ở §A.3 và §A.4 tồn tại và chạy trong CI.
- [ ] Có guideline, tập dev và tập test đã khóa.
- [ ] Có số đo intra-annotator agreement.
- [ ] Mỗi kết quả trong bài báo truy ngược được tới cấu hình và commit.

## 10. References

- CiteProof AI Project Documentation v1.0: §16.
- `02-market-user-research.md` §4.3d (dataset tiếng Anh sẵn có).
