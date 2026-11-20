# 02 — Market & User Research: CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 1 — Idea Validation & Research |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | Bản nháp trước; desk research sơ bộ đã thực hiện ngày 20/11/2026 |
| Tài liệu liên quan | `01-idea-analysis.md`, `03-feasibility-study.md` |

---

## 1. Overview

Tài liệu Phase 1 về vấn đề, người dùng, giải pháp hiện có và định vị. Gồm hai phần:

1. **Kết quả tra cứu sơ bộ** (desk research) ngày 20/11/2026 về công cụ và nghiên cứu liên quan. Phần này đã có phát hiện làm thay đổi định vị nghiên cứu (§4.4).
2. **Kế hoạch và mẫu ghi nhận** cho nghiên cứu người dùng mà bạn thực hiện trong Phase 1.

## 2. Objectives

- Kiểm chứng các giả định A-01, A-03, A-04 (người dùng), A-06 (khác biệt), A-07 (đóng góp nghiên cứu), A-09 (pilot).
- Xây persona dựa trên dữ liệu thật thay vì giả định.
- Chốt định vị sản phẩm và định vị nghiên cứu.

## 3. Scope

**Trong phạm vi:** công cụ và nghiên cứu liên quan; người dùng Stage 1; phân tích mẫu bài thực tế.

**Ngoài phạm vi:** định giá, kế hoạch marketing, thị trường B2B (sau MVP).

## 4. Detailed Analysis

### 4.1 Vấn đề từ góc nhìn người dùng `[GĐ]`

| ID | Pain point | Bằng chứng hiện có | Kiểm chứng bằng |
|---|---|---|---|
| PP-1 | Tài liệu tham khảo sai thông tin hoặc bịa đặt, nhất là khi dùng AI hỗ trợ viết | Vấn đề đã được ghi nhận: các bài giới thiệu CheckIfExist và AtomGPT reference checker dẫn một khảo sát phát hiện khoảng 100 trích dẫn bịa đặt trong hơn 50 bài đã được chấp nhận tại NeurIPS 2025. Các nghiên cứu cũ hơn được tổng hợp trong bài CheckIfExist ghi nhận từ 25% đến 54% tài liệu tham khảo có ít nhất một sai sót | Phỏng vấn; mẫu bài |
| PP-2 | Trích dẫn có thật nhưng không hỗ trợ nhận định (diễn giải quá mức, trích sai ý) | Nhiều công trình 2025–2026 coi đây là bài toán riêng, gọi là claim–citation alignment (§4.3d) | Phỏng vấn; mẫu bài |
| PP-3 | Rà soát thủ công tốn thời gian | Suy luận | Phỏng vấn |
| PP-4 | Giảng viên khó kiểm tra kỹ trích dẫn trong khóa luận | Suy luận | Phỏng vấn giảng viên, nếu tiếp cận được |

### 4.2 Persona `[GĐ]`

**P1: Sinh viên năm cuối ngành CNTT** (Stage 1, người dùng chính)

| Khía cạnh | Mô tả |
|---|---|
| Bối cảnh | Viết khóa luận tiếng Việt; trích chủ yếu bài tiếng Anh, thêm một số tài liệu tiếng Việt, sách, trang web; có dùng AI để tìm tài liệu và viết |
| Mục tiêu | Nộp khóa luận không bị bắt lỗi trích dẫn; bảo vệ suôn sẻ |
| Pain point | Không chắc tài liệu AI gợi ý có thật; không đủ thời gian đọc lại từng nguồn |
| Hành vi hiện tại | Tra thủ công trên Google Scholar, hoặc không kiểm tra |
| Thành công nghĩa là | Một lần chạy cho ra danh sách vấn đề cần sửa, kèm bằng chứng |

**P2: Học viên cao học / nghiên cứu sinh CS–AI** (Stage 1 phụ, Stage 2 chính)

| Khía cạnh | Mô tả |
|---|---|
| Bối cảnh | Viết bài tiếng Anh nộp hội nghị hoặc tạp chí; trích nhiều bài arXiv |
| Pain point | Sợ diễn giải sai kết quả của bài được trích; bị reviewer phát hiện |
| Thành công nghĩa là | Phát hiện các nhận định diễn giải quá mức trước khi nộp |

**P3: Giảng viên hướng dẫn** (người có ảnh hưởng)

| Khía cạnh | Mô tả |
|---|---|
| Bối cảnh | Duyệt nhiều khóa luận; không thể kiểm tra hết trích dẫn |
| Vai trò | Có thể là kênh giới thiệu công cụ cho sinh viên |
| Lưu ý | Không định vị công cụ như công cụ "bắt lỗi" sinh viên, vì rủi ro gán nhãn sai (R-06) |

### 4.3 Giải pháp hiện có và nghiên cứu liên quan (tra cứu sơ bộ 20/11/2026)

Thông tin lấy từ trang chính thức và bài báo công khai tại thời điểm tra cứu. Cần dùng thử thực tế (T-002) trước khi kết luận về tính năng của từng công cụ.

#### a) Nền tảng phân tích trích dẫn

| Công cụ | Chức năng liên quan | Khoảng trống so với CiteProof `[GĐ]` |
|---|---|---|
| scite.ai | Smart Citations phân loại câu trích dẫn thành hỗ trợ, trái chiều hoặc chỉ nhắc đến, kèm ngữ cảnh. Tính năng Reference Check cho phép tải bản thảo lên để xem tài liệu tham khảo nào bị rút bài, có thông báo biên tập, hoặc có bằng chứng trái chiều. Có quyền truy cập cả toàn văn trả phí nhờ hợp tác với nhà xuất bản | Theo mô tả công khai, Reference Check cho biết tài liệu được trích được giới khoa học nhìn nhận thế nào, không phải tài liệu đó có hỗ trợ đúng câu trong bản thảo hay không. Không thấy đề cập hỗ trợ tiếng Việt. Cần dùng thử để xác nhận |

#### b) Trợ lý nghiên cứu AI

| Công cụ | Chức năng liên quan | Khoảng trống |
|---|---|---|
| Elicit | Tìm và tổng hợp tài liệu, hỗ trợ systematic review, trích xuất dữ liệu từ bài báo, tạo báo cáo; dựa trên kho Semantic Scholar | Phục vụ khám phá và tổng hợp tài liệu, không phải rà soát trích dẫn trong bản thảo |
| Consensus | Chưa kiểm chứng trong đợt tra cứu này | Kiểm chứng ở T-002 |

#### c) Công cụ phát hiện tài liệu tham khảo bịa đặt (cùng hướng với phương án A)

Nhóm này phát triển nhanh trong 2025–2026, phần lớn là mã nguồn mở:

| Công cụ | Đặc điểm |
|---|---|
| CheckIfExist | Công cụ web mã nguồn mở, kiểm tra tài liệu tham khảo theo thời gian thực trên nhiều cơ sở dữ liệu học thuật |
| HalluCiteChecker | Bộ công cụ nhẹ, chạy offline trên CPU |
| Hallucinator | Công cụ mã nguồn mở của Boston University, trích xuất trích dẫn và đối chiếu với nhiều cơ sở dữ liệu |
| AtomGPT reference checker | Dùng LLM tách trường thông tin rồi đối chiếu với Semantic Scholar, trả về ba mức: đã xác minh, một phần, có khả năng bịa đặt |
| PAU | Mã nguồn mở, kết hợp kiểm tra metadata với đánh giá mức khớp ngữ nghĩa bằng LLM |
| check-hallucinations | Công cụ dòng lệnh kiểm tra file BibTeX với Semantic Scholar |
| HalRef, RefChecker | Được nhắc trong bài đánh giá của Badalova và Mayr (2026); chưa tra cứu chi tiết |

Một position paper năm 2026 đánh giá một số công cụ trong nhóm này. Kết luận: chúng hữu ích như cảnh báo sớm, nhưng bị giới hạn bởi lỗi trích xuất tài liệu tham khảo, metadata thiếu, độ phủ cơ sở dữ liệu hạn chế và kết quả thiếu nhất quán.

#### d) Nghiên cứu về citation fidelity (cùng hướng với phương án B)

| Công trình | Nội dung |
|---|---|
| CiteAudit (2026) | Pipeline multi-agent (trích claim, truy xuất bằng chứng, khớp đoạn, suy luận, phán quyết) kiểm tra nguồn được trích có thực sự hỗ trợ claim không; kèm dataset lớn có người xác nhận và một ứng dụng web |
| DeepSciVerify (2026) | Kiểm chứng hai giai đoạn: xét abstract trước, chỉ chuyển sang đoạn toàn văn khi abstract không đủ; đánh giá trên dataset SCitance |
| SemanticCite (2025) | Kiểm chứng dựa trên toàn văn, có lớp "hỗ trợ một phần"; công bố dataset hơn 1.000 trích dẫn thuộc tám ngành |
| CoVeGAT (2025) | Dataset citation alignment khoảng 500 cặp claim–trích dẫn đa lĩnh vực, kèm pipeline lai |
| CiteCheck (2025) | Dataset lớn tiếng Trung về citation faithfulness trong bối cảnh RAG |

#### e) Kiểm chứng thông tin tiếng Việt

| Dataset | Nội dung |
|---|---|
| ViWikiFC | Fact-checking tiếng Việt dựa trên Wikipedia, khoảng 20 nghìn claim |
| ViFactCheck | Fact-checking tin tức tiếng Việt đa chủ đề, hơn 7 nghìn cặp claim–bằng chứng |
| ISE-DSC01 | Kiểm chứng thông tin tiếng Việt từ cuộc thi UIT Data Science Challenge |

Đợt tra cứu này chưa tìm thấy công trình về citation fidelity cho bài khoa học tiếng Việt, hay giữa claim tiếng Việt và nguồn tiếng Anh. Đây chỉ là kết quả sơ bộ; cần literature review đầy đủ (T-001) trước khi kết luận.

### 4.4 Hệ quả đối với định vị

| Phát hiện | Hệ quả | Đề xuất `[ĐX]` |
|---|---|---|
| Phương án A đã có nhiều công cụ miễn phí, mã nguồn mở | A không thể là điểm khác biệt | Vẫn làm A vì là nền của B, nhưng cân nhắc tái sử dụng thành phần mã nguồn mở (ADR-008). Khác biệt nằm ở trạng thái trung thực cho nguồn trong nước |
| CiteAudit đã dùng pipeline multi-agent cho đúng bài toán này | Kiến trúc multi-agent của v1.0 không còn là điểm mới | Củng cố S-5 (`01`): multi-agent chỉ là biến thể so sánh. Dùng CiteAudit làm công trình đối chiếu |
| DeepSciVerify đã nghiên cứu trục abstract → toàn văn | Trục "điều kiện truy cập" của NC-2 không còn mới khi đứng riêng | Giữ làm yếu tố phân tích phụ |
| Đã có dataset tiếng Anh cho bài toán (SCitance, dataset của CiteAudit, SemanticCite, CoVeGAT) | Giảm khối lượng gán nhãn tiếng Anh (phương án E4) | Kiểm tra giấy phép và độ phù hợp lĩnh vực ở T-E02 |
| Chưa thấy công trình tiếng Việt hay xuyên ngôn ngữ Việt–Anh | Khoảng trống tiềm năng `[GĐ]` | Đặt trục xuyên ngôn ngữ làm trục nghiên cứu chính (NC-2 đã cập nhật trong `00`) |

**Định vị sản phẩm** `[ĐX]`:

> Với sinh viên và nhà nghiên cứu Việt Nam ngành CS–AI, CiteProof AI rà soát trích dẫn trong chính bản thảo của họ, viết bằng tiếng Việt hoặc tiếng Anh. Công cụ cho biết từng tài liệu tham khảo có tìm thấy được không, và từng nhận định có thực sự được tài liệu được trích hỗ trợ không, kèm bằng chứng. Nó không bao giờ kết luận "bịa đặt" khi tài liệu chỉ là chưa tìm thấy.

**Định vị nghiên cứu** `[ĐX]`: kiểm chứng citation fidelity xuyên ngôn ngữ Việt–Anh trong lĩnh vực CS–AI, kèm benchmark cho bối cảnh này, nếu literature review xác nhận chưa có công trình tương tự.

### 4.5 Kế hoạch nghiên cứu người dùng

#### 4.5.1 Phỏng vấn

- **Đối tượng:** sinh viên năm cuối và học viên cao học CS–AI; giảng viên nếu tiếp cận được.
- **Số lượng** `[ĐX]`: bắt đầu với 5–8 người, dừng khi câu trả lời bắt đầu lặp lại.
- **Hình thức:** 20–30 phút; xin phép trước nếu ghi âm.
- **Nguyên tắc:** hỏi về hành vi trong quá khứ, không hỏi kiểu "bạn có muốn công cụ X không".

Bộ câu hỏi:

1. Bạn đang hoặc đã viết loại bài nào (tiểu luận, khóa luận, bài báo)? Bằng ngôn ngữ nào?
2. Bạn tìm tài liệu tham khảo bằng cách nào? Có dùng AI không?
3. Kể lại lần gần nhất bạn kiểm tra lại tài liệu tham khảo trước khi nộp. Bạn làm gì, mất bao lâu?
4. Bạn đã bao giờ bị nhận xét là trích dẫn sai hoặc không đúng ý chưa? Chuyện gì đã xảy ra?
5. Khi trích một kết quả từ bài khác, bạn đọc toàn văn hay chỉ đọc abstract?
6. Tài liệu tham khảo của bạn thường gồm những loại nào? Có tài liệu tiếng Việt không?
7. Bạn có lưu PDF của các tài liệu mình trích không? Bạn có sẵn lòng tải chúng lên một công cụ để kiểm tra không?
8. Bạn lo ngại điều gì khi tải bản thảo chưa nộp lên một công cụ trực tuyến?
9. Nếu công cụ báo một trích dẫn có thể không hỗ trợ nhận định, bạn cần thấy gì để tin kết quả đó?
10. Bạn có sẵn lòng dùng thử và góp ý cho một prototype không?

Với giảng viên, hỏi thêm: cách họ kiểm tra trích dẫn khi chấm bài, và lo ngại về việc công cụ gán nhãn sai cho sinh viên.

#### 4.5.2 Phân tích mẫu bài thực tế

- **Mẫu** `[ĐX]`: 10–20 bài CS–AI thực tế, gồm bài tiếng Việt (khóa luận, bài tạp chí công khai) và bài tiếng Anh (arXiv).
- Chỉ dùng tài liệu công khai hoặc được chủ sở hữu đồng ý.
- Với mỗi tài liệu tham khảo, ghi nhận: loại tài liệu, ngôn ngữ, có DOI không, có trong nguồn metadata không, có toàn văn mở không.
- Mẫu này được dùng lại cho gán nhãn thử (T-009) và kiểm thử parsing (T-208).

### 4.6 Mẫu ghi nhận kết quả (điền trong Phase 1)

**Tổng hợp phỏng vấn**

| Người | Vai trò | Ngôn ngữ bài | Pain point nổi bật | Trích dẫn đáng chú ý (diễn giải) | Sẵn lòng pilot |
|---|---|---|---|---|---|
| | | | | | |

**Thống kê mẫu bài**

| Chỉ số | Bài tiếng Việt | Bài tiếng Anh |
|---|---|---|
| Số bài | | |
| Tổng số tài liệu tham khảo | | |
| Tỷ lệ bài báo / sách / luận văn / web / khác | | |
| Tỷ lệ tài liệu tiếng Anh / tiếng Việt | | |
| Tỷ lệ có DOI | | |
| Tỷ lệ tìm thấy trong nguồn metadata | | |
| Tỷ lệ có toàn văn mở | | |

**Kết luận cho từng giả định**

| Giả định | Kết luận (Xác nhận / Bác bỏ / Chưa rõ) | Bằng chứng |
|---|---|---|
| A-01 | | |
| A-02 | | |
| A-03 | | |
| A-04 | | |
| A-06 | | |
| A-07 | | |
| A-09 | | |

## 5. Implementation Steps

- [ ] T-001: Literature review đầy đủ, bắt đầu từ các công trình ở §4.3d và §4.3e.
- [ ] T-002: Dùng thử scite Reference Check và ít nhất hai công cụ ở §4.3c trên cùng một bài mẫu.
- [ ] T-003: Thu thập và phân tích mẫu bài (§4.5.2).
- [ ] T-004: Phỏng vấn người dùng (§4.5.1).
- [ ] Điền các bảng ở §4.6 và cập nhật persona, định vị.

## 6. Expected Outputs

- Bảng so sánh giải pháp hiện có, đã kiểm chứng bằng dùng thử.
- Persona cập nhật theo dữ liệu phỏng vấn.
- Thống kê mẫu bài thực tế.
- Định vị sản phẩm và nghiên cứu được chốt.

## 7. Dependencies

| Phụ thuộc | Mô tả |
|---|---|
| `01` §4.8 | Danh sách giả định cần kiểm chứng |
| `03` | Dùng thống kê mẫu bài và kết luận giả định cho đánh giá khả thi |
| Người tham gia phỏng vấn | Cần kênh tiếp cận (A-09) |

## 8. Risks and Considerations

| Rủi ro | Giảm thiểu |
|---|---|
| Mẫu phỏng vấn thiên lệch (chỉ bạn bè) | Đa dạng nguồn tiếp cận; ghi rõ giới hạn |
| Câu hỏi dẫn dắt làm sai lệch kết quả | Dùng bộ câu hỏi về hành vi trong quá khứ (§4.5.1) |
| Lĩnh vực phát triển nhanh, tra cứu nhanh lỗi thời | Ghi ngày tra cứu; tra lại trước khi viết bài báo |
| Sử dụng bài của người khác khi chưa được phép | Chỉ dùng bài công khai hoặc có đồng ý; ẩn danh khi trình bày |

## 9. Acceptance Criteria

- [ ] Có ít nhất một nguồn bằng chứng (phỏng vấn hoặc mẫu bài) cho mỗi pain point PP-1 đến PP-4.
- [ ] Mỗi giả định A-01, A-03, A-04, A-06, A-07, A-09 có kết luận ở §4.6.
- [ ] Bảng giải pháp hiện có đã được kiểm chứng bằng dùng thử, có ngày kiểm tra.
- [ ] Định vị sản phẩm và nghiên cứu được bạn xác nhận.

## 10. References

Tra cứu ngày 20/11/2026.

- scite — Features: https://scite.ai/features
- scite — Tổng quan sản phẩm: https://scite.ai/llms.txt
- Elicit — Systematic Review: https://elicit.com/blog/systematic-review
- Deakin University Library — Đánh giá Elicit: https://deakin.libguides.com/AI-Evaluations/Elicit
- CheckIfExist: https://www.arxiv.org/pdf/2602.15871
- Badalova & Mayr (2026), Detecting Hallucinated and Suspicious Citations: https://arxiv.org/pdf/2607.22693
- HalluCiteChecker: https://arxiv.org/pdf/2604.26835
- AtomGPT reference checker: https://arxiv.org/abs/2607.09774
- PAU (AIES): https://ojs.aaai.org/index.php/AIES/article/download/43316/50876/47422
- Hallucinator (Boston University): https://www.bu.edu/eng/2026/06/17/fictional-bibliographies/
- check-hallucinations: https://pypi.org/project/check-hallucinations/
- CiteAudit: https://arxiv.org/abs/2602.23452
- DeepSciVerify: https://arxiv.org/abs/2605.27710
- SemanticCite: https://arxiv.org/pdf/2511.16198
- CoVeGAT: https://aclanthology.org/anthology-files/pdf/ethicalllms/2025.ethicalllms-1.2.pdf
- CiteCheck: https://arxiv.org/html/2502.10881v1
- ViWikiFC: https://arxiv.org/abs/2405.07615v1
- ViFactCheck: https://arxiv.org/abs/2412.15308v1
- ISE-DSC01 (qua bài BERT-based model for Vietnamese Fact Verification): https://arxiv.org/abs/2503.00356v1
