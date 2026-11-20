# 03 — Feasibility Study: CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 1 — Idea Validation & Research |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | Bản nháp trước |
| Tài liệu liên quan | `01-idea-analysis.md`, `02-market-user-research.md`, `07-technical-architecture.md` |

---

## 1. Overview

Tài liệu đánh giá CiteProof AI (phiên bản MVP) có khả thi hay không ở sáu khía cạnh: kỹ thuật, dữ liệu, nghiên cứu, nguồn lực, pháp lý – đạo đức, kinh doanh. Tài liệu cũng đặt ra các tiêu chí go/no-go và hướng chuyển đổi (pivot), để kết thúc Phase 1 bằng một quyết định rõ ràng.

## 2. Objectives

- Xác định các thành phần rủi ro kỹ thuật cao và kiểm chứng sớm bằng spike.
- Xác định nguồn dữ liệu dùng được và điều kiện truy cập.
- Đánh giá rủi ro về tính mới của phần nghiên cứu.
- Đánh giá thời gian và chi phí cho một người.
- Đặt tiêu chí go/no-go trước khi đo, để tránh điều chỉnh ngưỡng theo kết quả.

## 3. Scope

**Trong phạm vi:** phạm vi MVP theo D-04.

**Ngoài phạm vi:** khả thi của các tính năng sau MVP (contradiction detection, B2B).

## 4. Detailed Analysis

### 4.1 Khả thi kỹ thuật

| Thành phần | Hướng tiếp cận ứng viên `[ĐX]` | Độ khó | Rủi ro chính | Spike |
|---|---|---|---|---|
| Parse PDF, tách tài liệu tham khảo | GROBID (công cụ mã nguồn mở chuyên phân tích bài báo khoa học dạng PDF), PyMuPDF làm dự phòng | Trung bình | GROBID tối ưu cho bài tiếng Anh định dạng chuẩn; khóa luận tiếng Việt có thể parse kém | SPK-01 |
| Parse DOCX | python-docx | Thấp–TB | Trích dẫn gõ tay không theo chuẩn | SPK-01 |
| Phát hiện marker trích dẫn, liên kết với tài liệu tham khảo | Rule-based cho kiểu số `[1]` và tác giả–năm; LLM dự phòng | TB | Tên người Việt (họ đứng trước); nhiều kiểu trích dẫn | SPK-01 |
| Tra cứu metadata | Crossref, OpenAlex, Semantic Scholar; cân nhắc tái sử dụng công cụ mã nguồn mở (`02` §4.3c) | Thấp–TB | Giới hạn tốc độ API; tài liệu trong nước không có trong các nguồn này | SPK-02 |
| Tìm toàn văn mở | arXiv, ACL Anthology, thông tin bản mở từ OpenAlex/Unpaywall, Semantic Scholar | TB | Tỷ lệ thực tế chưa biết (A-02); PDF lấy về có thể hỏng | SPK-03 |
| Truy xuất bằng chứng xuyên ngôn ngữ | Embedding đa ngôn ngữ (ví dụ BGE-M3, multilingual-e5) hoặc dịch claim sang tiếng Anh trước | TB–Cao | Chất lượng với thuật ngữ chuyên ngành | SPK-04 |
| Phán quyết claim–bằng chứng | LLM thương mại, đầu ra có cấu trúc, kiểm tra trích dẫn nguyên văn | TB | Hallucination; chi phí | SPK-04, SPK-05 |
| Báo cáo và giao diện | Server-rendered (`07`, ADR-005) | Thấp–TB | Khối lượng việc front-end | — |

**Danh sách spike** (thử nghiệm ngắn, mã nguồn bỏ đi sau khi có kết luận):

| ID | Mục tiêu | Cách làm | Đầu ra | Thời lượng `[ĐX]` |
|---|---|---|---|---|
| SPK-01 | Đánh giá parse PDF/DOCX và tách tài liệu tham khảo | Chạy GROBID và PyMuPDF trên mẫu bài của T-003, gồm bài tiếng Việt và tiếng Anh | Tỷ lệ tách đúng tài liệu tham khảo và marker; danh sách lỗi điển hình | 1–2 ngày |
| SPK-02 | Đo độ phủ tra cứu metadata | Tra các tài liệu tham khảo trong mẫu qua Crossref, OpenAlex, Semantic Scholar | Tỷ lệ tìm thấy theo loại và ngôn ngữ tài liệu | 1 ngày |
| SPK-03 | Đo tỷ lệ toàn văn mở | Với tài liệu tìm thấy, thử lấy bản mở | Tỷ lệ có toàn văn mở; nguồn hiệu quả nhất | 1 ngày |
| SPK-04 | Thử phán quyết xuyên ngôn ngữ | Lấy một nhóm nhỏ cặp claim–nguồn; chạy claim tiếng Anh và bản tiếng Việt qua cùng LLM, cả hai chế độ trực tiếp và dịch trước | So sánh định tính và số đúng/sai trên mẫu nhỏ | 1–2 ngày |
| SPK-05 | Ước tính chi phí và thời gian | Đo số token và độ trễ cho một bài hoàn chỉnh | Chi phí ước tính mỗi bài; đầu vào cho Q-01 và NFR-02 | 0,5 ngày |

### 4.2 Khả thi dữ liệu

| Nguồn | Vai trò | Cần kiểm tra ở Phase 1 |
|---|---|---|
| Crossref | Metadata theo DOI | Điều khoản và cách truy cập khuyến nghị |
| OpenAlex | Metadata, thông tin bản mở | Điều kiện truy cập hiện hành |
| Semantic Scholar | Metadata, liên kết PDF mở | Khóa API; API công khai có giới hạn tốc độ |
| Unpaywall | Tìm bản mở theo DOI | Điều kiện sử dụng |
| arXiv | Toàn văn CS–AI | Điều khoản truy cập hàng loạt |
| ACL Anthology | Toàn văn NLP | Cách tải |
| VJOL (Vietnam Journals Online) | Tạp chí Việt Nam: nền tảng do Cục Thông tin, Thống kê (Bộ KH&CN) quản lý, liệt kê khoảng 359 tạp chí, nhiều bài có toàn văn; một số tạp chí có tiền tố DOI | Khả năng truy cập bằng máy (nền tảng OJS) `[GĐ]`; độ phủ tạp chí CS–AI |
| Tài liệu người dùng tải lên | Toàn văn bất kỳ | Quyền riêng tư, bản quyền (§4.5) |

Tài liệu này không ghi con số giới hạn tốc độ hay điều khoản cụ thể khi chưa đọc tài liệu chính thức của từng nguồn.

### 4.3 Khả thi nghiên cứu

**Rủi ro tính mới** (từ `02` §4.4):

| Trục nghiên cứu | Đánh giá |
|---|---|
| Multi-agent cho citation fidelity | Rủi ro cao: CiteAudit đã làm |
| Abstract so với toàn văn | Rủi ro cao: DeepSciVerify đã làm |
| Xuyên ngôn ngữ Việt–Anh, bối cảnh khóa luận Việt Nam | Khoảng trống tiềm năng `[GĐ]`, cần T-001 xác nhận |

**Các đóng góp khả thi, xếp theo mức độ phù hợp với nguồn lực** `[ĐX]`:

1. Benchmark nhỏ nhưng cẩn thận cho citation fidelity tiếng Việt và Việt–Anh trong CS–AI.
2. So sánh thực nghiệm các chiến lược xuyên ngôn ngữ (trực tiếp so với dịch trước) và các biến thể pipeline.
3. Hệ thống kèm kết quả pilot thực tế, với cơ chế trạng thái trung thực cho tài liệu không tìm thấy.

**Dữ liệu đánh giá:** đã có dataset tiếng Anh (giấy phép chưa kiểm tra); dữ liệu tiếng Việt phải tự xây; gán nhãn một người theo chiến lược ở `13` §B.

### 4.4 Khả thi nguồn lực

**Thời gian.** Roadmap (`11`) giả định bạn làm gần toàn thời gian `[GĐ]`. Số giờ mỗi tuần là câu hỏi Q-10. Nếu chỉ làm bán thời gian, cần kéo dài roadmap hoặc cắt phạm vi theo `11` §4.5.

**Kỹ năng** — bạn tự đánh giá:

| Kỹ năng | Dùng ở | Mức hiện tại (tự điền) | Cần học thêm |
|---|---|---|---|
| Python backend (FastAPI, SQLAlchemy) | M1–M6 | | |
| Xử lý PDF và văn bản | M2, M4 | | |
| LLM, prompt, RAG | M4–M5 | | |
| Thiết kế thực nghiệm và đánh giá | Evaluation track, M7 | | |
| Web UI cơ bản | M3, M6 | | |
| Docker, triển khai | M1, M6, M8 | | |
| Viết bài khoa học | M9 | | |

**Chi phí LLM.** Công thức ước tính:

```text
Chi phí mỗi bài ≈ Σ (theo claim) [số lần gọi × (token vào × đơn giá vào + token ra × đơn giá ra)]
               + chi phí các lần gọi cho tài liệu tham khảo (parse dự phòng, dịch)
Chi phí thực nghiệm ≈ số mục đánh giá × số biến thể × chi phí mỗi mục
```

Điền số liệu sau SPK-05 và đối chiếu với ngân sách (Q-01). Cache kết quả LLM để chạy lại thực nghiệm mà không tốn thêm chi phí.

**Hạ tầng.** GROBID chạy trên Java và cần lượng RAM đáng kể `[GĐ]`; kiểm tra yêu cầu thực tế khi làm SPK-01 để chọn cấu hình máy ở `14`.

### 4.5 Khả thi pháp lý và đạo đức

Phần này không phải tư vấn pháp lý; cần tìm hiểu thêm hoặc hỏi người có chuyên môn khi chuẩn bị pilot.

| Vấn đề | Phân tích | Hướng xử lý `[ĐX]` |
|---|---|---|
| Bản quyền toàn văn | Giấy phép của bản mở rất khác nhau; không phải bài nào có bản mở cũng cho phép phân phối lại | Chỉ lưu để xử lý; chỉ hiển thị đoạn trích ngắn kèm nguồn; không cho tải lại toàn văn; ghi giấy phép khi có |
| Tài liệu người dùng tải lên | Có thể là bài có bản quyền | Chỉ người tải lên truy cập được; không dùng chung giữa người dùng |
| Bản thảo chưa công bố | Gửi tới nhà cung cấp LLM bên ngoài | Kiểm tra chính sách dữ liệu của nhà cung cấp; thông báo và xin đồng ý (FR-63); cho phép xóa |
| Dữ liệu cá nhân | Email, bản thảo | Tìm hiểu quy định bảo vệ dữ liệu cá nhân hiện hành của Việt Nam; thu thập tối thiểu |
| Điều khoản API | Mỗi nguồn có điều khoản riêng | Tuân thủ, cache hợp lý, tôn trọng giới hạn tốc độ |
| Gán nhãn sai cho người dùng | Báo "không tìm thấy" có thể bị hiểu là gian lận | Trạng thái và cách diễn đạt theo `04` §4.5 |
| Dùng bài của người khác cho đánh giá | Khóa luận thường là của sinh viên khác | Chỉ dùng bài công khai hoặc có đồng ý; ẩn danh |

### 4.6 Khả thi kinh doanh (mức sơ bộ)

- Phương án A có nhiều lựa chọn miễn phí, nên giá trị phải đến từ B và hỗ trợ tiếng Việt.
- Giá trị của B chưa được chứng minh với người dùng thật (A-01).
- Willingness to pay chỉ cần tín hiệu định tính trong pilot (câu hỏi phỏng vấn sau khi dùng thử).
- Hướng tổ chức (trường, khoa) là dài hạn; pilot có thể tạo quan hệ ban đầu với giảng viên.

### 4.7 Tiêu chí go / no-go / pivot

Điền ngưỡng **trước khi đo**, để tránh điều chỉnh ngưỡng theo kết quả.

| # | Câu hỏi | Tín hiệu đo | Ngưỡng (bạn điền) | Nếu đạt | Nếu không đạt |
|---|---|---|---|---|---|
| G-1 | Người dùng có thấy PP-2 là vấn đề thật? | Phỏng vấn | ___ / ___ người mô tả được tình huống cụ thể | Giữ trọng tâm B | Cân nhắc chuyển trọng tâm sang rà soát tài liệu tham khảo cho nguồn Việt Nam, hoặc nghiêng hẳn về nghiên cứu |
| G-2 | Tỷ lệ tài liệu được trích có toàn văn mở (CS–AI) | SPK-03 | ≥ ___% | Giữ thiết kế | Đẩy mạnh luồng tải lên; cân nhắc thu hẹp về ML/NLP (Q-04) |
| G-3 | LLM phán quyết claim tiếng Việt với bằng chứng tiếng Anh | SPK-04 | Chênh lệch so với claim tiếng Anh ≤ ___ trên mẫu nhỏ | Dùng chế độ trực tiếp | Dùng chế độ dịch trước; khoảng chênh trở thành một kết quả nghiên cứu |
| G-4 | Tính mới của trục xuyên ngôn ngữ | T-001 | Chưa có công trình tương tự | Giữ định vị nghiên cứu | Chuyển đóng góp sang benchmark, hệ thống, hoặc đánh giá thực tế |
| G-5 | Parse khóa luận tiếng Việt | SPK-01 | Tách đúng ≥ ___% tài liệu tham khảo | Dùng GROBID | Thêm parser LLM; giới hạn định dạng đầu vào |
| G-6 | Người dùng pilot | Phỏng vấn | ≥ ___ người đồng ý dùng thử | Giữ mục tiêu sản phẩm | Ưu tiên nghiên cứu; làm demo thay cho pilot |
| G-7 | Thời gian gán nhãn | T-009 | Đủ cho tập test tối thiểu trong quỹ thời gian | Giữ kế hoạch | Thu hẹp tập test; dựa nhiều hơn vào dataset sẵn có |

**Kết luận Phase 1** (chọn một): GO / GO kèm điều chỉnh / PIVOT. Ghi quyết định vào Decision Log.

## 5. Implementation Steps

- [ ] Điền ngưỡng ở §4.7 trước khi bắt đầu đo.
- [ ] Chạy SPK-01 đến SPK-05 (T-005 đến T-008, cùng phần đo chi phí).
- [ ] Kiểm tra điều khoản các nguồn dữ liệu ở §4.2.
- [ ] Tự đánh giá kỹ năng ở §4.4.
- [ ] Trả lời Q-01, Q-03, Q-10.
- [ ] Ra quyết định go / no-go / pivot và cập nhật Decision Log.

## 6. Expected Outputs

- Kết quả năm spike, kèm số liệu đo được.
- Bảng nguồn dữ liệu đã kiểm tra điều khoản.
- Quyết định go / no-go / pivot có căn cứ.

## 7. Dependencies

| Phụ thuộc | Mô tả |
|---|---|
| `02` | Mẫu bài, kết quả phỏng vấn, đánh giá tính mới |
| `07` | Phương án kỹ thuật được thử trong spike |
| Ngân sách | Q-01, ảnh hưởng SPK-04, SPK-05 |

## 8. Risks and Considerations

| Rủi ro | Giảm thiểu |
|---|---|
| Spike kéo dài thành phát triển thật | Giới hạn thời gian cho mỗi spike; mã nguồn spike không đưa vào sản phẩm |
| Mẫu đo quá nhỏ để kết luận | Diễn giải kết quả spike là tín hiệu, không phải kết luận thống kê |
| Ngưỡng go/no-go bị nới sau khi thấy kết quả | Ghi ngưỡng vào tài liệu trước khi đo, có ngày ghi |

## 9. Acceptance Criteria

- [ ] Mỗi thành phần ở §4.1 có kết luận khả thi (có / có điều kiện / không).
- [ ] Mỗi nguồn ở §4.2 đã được kiểm tra điều khoản truy cập.
- [ ] Có chi phí ước tính mỗi bài từ SPK-05.
- [ ] Mọi tiêu chí G-1 đến G-7 có kết quả đo và kết luận.
- [ ] Có quyết định go / no-go / pivot được ghi vào Decision Log.

## 10. References

- `02-market-user-research.md` §4.3 và danh mục nguồn ở §10.
- VJOL — About: https://vjol.info.vn/index/en/about/
- VJOL — Danh sách tạp chí: https://vjol.vista.gov.vn/index/en
- check-hallucinations (ghi chú về giới hạn tốc độ của API công khai Semantic Scholar): https://pypi.org/project/check-hallucinations/
