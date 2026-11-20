# 01 — Idea Analysis: CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1 |
| Phase | Phase 0 — Project Initialization |
| Ngày cập nhật | 20/11/2026 |
| Trạng thái | IN_REVIEW |
| Tài liệu liên quan | [`00-project-overview.md`](00-project-overview.md), [`project-status.md`](project-status.md) |

Quy ước `[XN]` (đã xác nhận), `[GĐ]` (giả định), `[ĐX]` (đề xuất) như trong `00-project-overview.md`. Mã quyết định D-xx và đề xuất P-xx tham chiếu Decision Log ở `00` §4.4.

---

## 1. Overview

Tài liệu này phân tích ý tưởng CiteProof AI dựa trên tài liệu v1.0 và ba vòng hỏi đáp của Phase 0. Nội dung gồm điểm mạnh cần giữ, các vấn đề chiến lược, mâu thuẫn nội bộ của v1.0, phân tích các ràng buộc mới, định vị nghiên cứu sơ bộ, cùng danh sách giả định và câu hỏi mở. Đây là đầu vào trực tiếp cho Phase 1.

## 2. Objectives

- Làm rõ lý do đằng sau các quyết định trong `00-project-overview.md`.
- Ghi nhận mọi mâu thuẫn trong v1.0 và trạng thái xử lý của từng mâu thuẫn.
- Xác định các giả định rủi ro cao cần kiểm chứng ở Phase 1.
- Đề xuất chiến lược đánh giá trong điều kiện chỉ có một người gán nhãn.

## 3. Scope

**Trong phạm vi:** phân tích ý tưởng, phạm vi, ràng buộc, định vị nghiên cứu sơ bộ, giả định và câu hỏi mở.

**Ngoài phạm vi:** nghiên cứu thị trường và đối thủ chi tiết (Phase 1, `02`), đánh giá khả thi có số liệu (Phase 1, `03`), quyết định công nghệ (Phase 3).

## 4. Detailed Analysis

### 4.1 Tóm tắt ý tưởng

Người viết bài nghiên cứu thường trích dẫn nhiều tài liệu, nhưng việc một tài liệu được trích không đảm bảo nó thực sự hỗ trợ nhận định tương ứng. Tài liệu tham khảo cũng có thể sai metadata hoặc không tìm thấy. CiteProof AI tự động hóa việc rà soát này: tách các nhận định có trích dẫn, đối chiếu với tài liệu được trích, và trả về kết luận có bằng chứng kèm giới hạn rõ ràng.

### 4.2 Điểm mạnh của v1.0 cần giữ

- **Trung thực về giới hạn:** không phán đúng/sai tuyệt đối; có trạng thái UNVERIFIABLE riêng.
- **Theo dõi trạng thái truy cập nguồn** (§9.3): phân biệt metadata, abstract, toàn văn mở, toàn văn do người dùng cung cấp.
- **Không dùng confidence tự sinh của LLM như xác suất đúng** (§11.4).
- **Truy xuất nguồn gốc cho từng kết luận** (§11.3).
- **Non-goals rõ ràng** (§2.3), giúp chống phình phạm vi.
- **Ý thức nghiên cứu:** baseline, ablation, khả năng tái lập (§16, §17).

### 4.3 Vấn đề chiến lược và cách xử lý

| # | Vấn đề | Cách xử lý | Trạng thái |
|---|---|---|---|
| S-1 | Dự án mang hai bản sắc (nghiên cứu và sản phẩm) với tiêu chí thành công khác nhau | Kết hợp, dùng chung một bộ đánh giá (D-01) | Đã xử lý |
| S-2 | Gộp citation fidelity và open-domain verification thành một bài toán | MVP chỉ làm citation fidelity (D-04) | Đã xử lý |
| S-3 | MVP quá lớn: 10 tính năng P0, 9 Agent | Ranh giới MVP (D-04); số lượng Agent đánh giá lại ở Phase 3 | Một phần |
| S-4 | Nhiều "Agent" thực chất là bước xử lý tất định (parse PDF, tra DOI), như chính v1.0 §6.1 thừa nhận | Phân biệt bước tất định và bước cần LLM khi thiết kế kiến trúc | Mở, Phase 3 |
| S-5 | Xây multi-agent trước khi có baseline là ngầm giả định câu trả lời cho RQ3 | Xây baseline trước; multi-agent đầy đủ chỉ là một biến thể so sánh nếu còn thời gian `[ĐX]` | Mở, Phase 3–4 |
| S-6 | Giới hạn toàn văn có thể khiến UNVERIFIABLE chiếm đa số kết quả | Tải lên tài liệu được trích (D-08); CS–AI có tỷ lệ toàn văn mở cao `[GĐ]` A-02 | Một phần, cần đo ở Phase 1 |
| S-7 | Ngôn ngữ và lĩnh vực chưa xác định | D-05, D-06 | Đã xử lý; phát sinh thách thức ở §4.5 |

### 4.4 Mâu thuẫn trong tài liệu v1.0

| # | Vị trí | Mâu thuẫn | Xử lý | Trạng thái |
|---|---|---|---|---|
| C-1 | §11.4 và §16.2 | Không dùng confidence LLM như xác suất, nhưng lại đo Calibration bằng Brier Score/ECE, vốn cần một xác suất để hiệu chỉnh | Reliability Assessment nằm ngoài MVP, nên đề xuất hoãn Calibration khỏi bộ đánh giá MVP `[ĐX]`. Khi đưa lại, phải định nghĩa rõ nguồn xác suất hoặc dùng đánh giá dạng risk–coverage | Mở, Phase 2 |
| C-2 | §8.1 và §4.2 | Workflow chính có Contradiction Search và Reliability Assessment, nhưng F09, F10 là P1 | Cả hai nằm ngoài MVP (D-04); workflow MVP được vẽ lại ở Phase 3 | Đã xử lý |
| C-3 | §8.2 và F14 | Có routing "yêu cầu human review" trong khi Human Review là P1 | Ở MVP, kết quả không chắc chắn được gắn cờ "nên tự kiểm tra" thay vì chuyển cho người duyệt `[ĐX]` | Mở, Phase 2 |
| C-4 | §3.3 và §18 | GTM bắt đầu bằng sinh viên dùng thử, nhưng Frontend nằm ở cuối roadmap | Đặt một giao diện tối giản cho pilot sớm hơn `[ĐX]` | Mở, Phase 4 |
| C-5 | §16.3 và §18 | Baseline và benchmark ở cuối roadmap, trong khi cần chúng từ sớm để đo tác động của từng thành phần | Tập đánh giá nhỏ và Single-LLM baseline ở các milestone đầu `[ĐX]` | Mở, Phase 4 |
| C-6 | Toàn bộ | Ba hệ "Phase" trùng tên: vòng đời Master Prompt, roadmap v1.0, GTM v1.0 | P-02 | Chờ xác nhận |
| C-7 | §23 | Danh sách tài liệu khác bộ đánh số của Master Prompt | D-09 | Đã xử lý |
| C-8 | §11 | Chỉ có trạng thái cho claim, chưa có trạng thái cho reference | Bản nháp ở §4.5.2; chốt trong PRD | Mở, Phase 2 |

### 4.5 Phân tích các ràng buộc

#### 4.5.1 Đầu vào song ngữ (D-06)

| Claim | Nguồn | Hỗ trợ trong MVP | Thách thức chính |
|---|---|---|---|
| Anh | Anh | Đầy đủ | Trường hợp chuẩn, dùng làm mốc so sánh |
| Việt | Anh | Đầy đủ `[ĐX]` | Truy xuất xuyên ngôn ngữ: tìm theo từ khóa (BM25) không khớp giữa hai ngôn ngữ; cần embedding đa ngôn ngữ hoặc dịch claim trước khi tìm (quyết định ở Phase 3) |
| Việt | Việt | Chỉ khi người dùng tải lên nguồn | Nguồn thường không truy cập được; metadata thiếu |
| Anh | Việt | Chỉ khi người dùng tải lên nguồn | Hiếm gặp |

Theo giả định A-04, bài tiếng Việt ngành CS–AI chủ yếu trích tài liệu tiếng Anh. Nếu đúng, hàng thứ hai là trường hợp quan trọng nhất sau hàng đầu tiên.

**Rủi ro parsing:** tài liệu tiếng Việt cũ có thể dùng bảng mã TCVN3 hoặc VNI thay vì Unicode, khiến văn bản trích xuất bị lỗi ký tự. Bộ kiểm thử parsing cần có mẫu loại này, và cần cân nhắc cơ chế phát hiện, chuyển mã hoặc ít nhất là cảnh báo.

**Đánh giá:** song ngữ làm khối lượng gán nhãn tăng gấp đôi. Đề xuất `[ĐX]`:

1. Xây tập đánh giá tiếng Anh trước.
2. Dịch các claim sang tiếng Việt, giữ nguyên nhãn, để đo mức giảm chất lượng khi đổi ngôn ngữ trên cùng một tập.
3. Bổ sung một tập nhỏ claim tiếng Việt viết tự nhiên, vì claim dịch không phản ánh đúng cách người Việt viết.

#### 4.5.2 Tài liệu tham khảo hỗn hợp (D-07)

| Loại tài liệu | Kiểm tra tồn tại và metadata | Kiểm tra hỗ trợ claim |
|---|---|---|
| Bài báo có DOI / được index | Tốt, qua các nguồn metadata như Crossref, OpenAlex | Được, nếu có bản Open Access hoặc người dùng tải lên |
| Bài tạp chí trong nước không có DOI | Chỉ best-effort | Chỉ khi người dùng tải lên |
| Sách | Tra theo ISBN/metadata, độ phủ không đồng đều | Mặc định không làm; chỉ khi người dùng tải lên trang liên quan |
| Luận văn | Khó, không có nguồn tra cứu thống nhất | Chỉ khi người dùng tải lên |
| Trang web | Kiểm tra URL còn hoạt động, có bản lưu trữ hay không | Lấy được nội dung trang, nhưng nội dung có thể đã khác lúc được trích |

**Nguyên tắc cốt lõi:** "không tìm thấy" không có nghĩa là "không tồn tại". Một luận văn thật của trường trong nước rất có thể không có trong các nguồn metadata quốc tế. Nếu hệ thống báo nó là tài liệu không tồn tại, người dùng bị nghi oan là bịa trích dẫn và sản phẩm mất uy tín rất nhanh.

**Bản nháp bộ trạng thái reference** `[ĐX]`, chốt ở Phase 2:

| Trạng thái | Ý nghĩa | Cách diễn đạt với người dùng |
|---|---|---|
| `MATCHED` | Tìm thấy bản ghi, metadata khớp | "Đã xác minh" kèm nguồn đã tra |
| `METADATA_MISMATCH` | Tìm thấy bản ghi tương ứng nhưng lệch một số trường (tác giả, năm, tiêu đề, DOI) | Chỉ rõ trường lệch và giá trị tìm thấy |
| `NOT_FOUND_IN_SEARCHED_SOURCES` | Không tìm thấy trong các nguồn đã tra | "Chưa tìm thấy trong [danh sách nguồn]"; không dùng từ "không tồn tại" hay "giả mạo" |
| `UNSUPPORTED_TYPE` | Loại tài liệu chưa có cơ chế tra cứu | Nêu loại tài liệu, gợi ý người dùng tự kiểm tra hoặc tải lên |
| `PARSE_FAILED` | Không tách được thông tin từ chuỗi tài liệu tham khảo | Hiển thị chuỗi gốc để người dùng kiểm tra |

Bộ trạng thái cố ý không có giá trị kiểu "giả mạo", vì hệ thống không đủ cơ sở để kết luận điều đó. Với trang web, bổ sung thông tin URL còn hoạt động hay không và có bản lưu trữ hay không.

#### 4.5.3 Không có người gán nhãn thứ hai (D-10)

**Hệ quả:**

- Không đo được inter-annotator agreement và không có bước adjudication như v1.0 §16.4 yêu cầu.
- Nhãn phản ánh cách hiểu của một người; sai lệch có hệ thống trong cách hiểu sẽ khó bị phát hiện.
- Khi công bố, phản biện nhiều khả năng sẽ đặt câu hỏi về độ tin cậy của nhãn.

**Các phương án:**

| ID | Phương án | Mô tả | Ưu điểm | Nhược điểm |
|---|---|---|---|---|
| E1 | Tự gán nhãn hai lượt | Gán nhãn lại một tập con sau một khoảng thời gian, đo độ nhất quán nội tại (intra-annotator agreement) | Khả thi một mình; có số liệu định lượng | Không thay thế được đồng thuận giữa nhiều người |
| E2 | Guideline chặt, nhãn kèm lý do | Viết annotation guideline trước; mỗi nhãn ghi kèm đoạn bằng chứng và lý do | Tăng tính tái lập; người khác có thể kiểm tra lại sau | Tốn thời gian hơn |
| E3 | Dữ liệu sinh có kiểm soát | Tạo ví dụ "không hỗ trợ" bằng thay đổi có chủ đích: đổi số liệu, khái quát hóa quá mức, hoán đổi tài liệu được trích | Nhãn biết trước; số lượng lớn; rẻ | Lỗi tổng hợp thường dễ hơn lỗi thật; câu gốc chưa chắc đã được hỗ trợ, nên vẫn phải kiểm tra thủ công một mẫu |
| E4 | Tận dụng dataset công khai | Dùng nếu Phase 1 tìm được dataset phù hợp | Nhãn từ nhiều người | Có thể không khớp bài toán, lĩnh vực hoặc ngôn ngữ |
| E5 | LLM tiền gán nhãn, người xác minh | LLM đề xuất nhãn, bạn duyệt lại | Nhanh hơn | Thiên lệch neo theo nhãn LLM; đánh giá bị vòng lặp nếu dùng cùng họ model với hệ thống được đánh giá |
| E6 | Thu nhãn từ người dùng pilot | Dùng phản hồi của người dùng trên kết quả | Nhãn từ người dùng thật | Ít, không kiểm soát được; chỉ có sau khi pilot |

**Đề xuất** `[ĐX]`, chốt ở Phase 1:

- Lấy E2 + E1 làm nền, E3 để mở rộng quy mô; thêm E4 nếu Phase 1 tìm được dataset phù hợp.
- E5 chỉ dùng để tăng tốc, phải báo cáo rõ, và không dùng cùng họ model với hệ thống được đánh giá.
- E6 để sau MVP.
- Mọi giới hạn được nêu trong phần Limitations của bài báo.
- Nếu sau này có người hỗ trợ, kể cả chỉ gán nhãn một phần nhỏ, ưu tiên dùng họ để đo agreement trên một tập con.

**Kích thước tập đánh giá:** không đặt con số trước khi có số đo. Ở Phase 1, gán nhãn thử một mẫu nhỏ, đo thời gian trung bình cho mỗi mục, rồi tính ngược từ quỹ thời gian dành cho gán nhãn.

### 4.6 Định vị nghiên cứu sơ bộ `[GĐ]`

**Bản nháp câu hỏi nghiên cứu chính:**

> Các cách tiếp cận dựa trên LLM khác nhau kiểm chứng hiệu quả đến đâu việc tài liệu được trích có hỗ trợ nhận định hay không (citation fidelity) trong lĩnh vực CS–AI, và hiệu quả đó thay đổi thế nào theo điều kiện truy cập nguồn (abstract hay toàn văn) và ngôn ngữ của nhận định (tiếng Anh hay tiếng Việt)?

**Ánh xạ với câu hỏi nghiên cứu của v1.0:**

| RQ v1.0 | Trong dự án hiện tại |
|---|---|
| RQ1: Claim extraction | Vai trò phụ. Trong citation fidelity, claim được neo bởi vị trí trích dẫn nên bài toán đơn giản hơn; vẫn cần xác định phần nào của câu được trích dẫn hỗ trợ |
| RQ2: Context-aware retrieval | Giữ, thu hẹp thành truy xuất đoạn bằng chứng trong một tài liệu đã biết, kể cả xuyên ngôn ngữ |
| RQ3: Multi-agent so với baseline | Thu hẹp: so sánh Single LLM, RAG, pipeline nhiều bước; multi-agent đầy đủ là tùy chọn |
| RQ4: Traceability và uncertainty | Giữ phần traceability; uncertainty nằm ngoài MVP |
| RQ5: Trade-off chất lượng, chi phí, độ trễ | Giữ nguyên |

**Không tuyên bố tính mới.** Mọi đóng góp tiềm năng, kể cả khía cạnh xuyên ngôn ngữ Việt–Anh, chỉ được khẳng định sau literature review, đúng nguyên tắc v1.0 §17.3.

**Cập nhật 20/11/2026:** tra cứu sơ bộ ở `02` §4.3–§4.4 cho thấy trục multi-agent (CiteAudit) và trục abstract/toàn văn (DeepSciVerify) đã có công trình liên quan. Trục xuyên ngôn ngữ Việt–Anh được đề xuất làm trục chính (NC-2 trong `00`).

### 4.7 Giải pháp hiện có (sơ bộ)

Một số công cụ có chồng lấn một phần, cần khảo sát ở Phase 1 (`02`): scite.ai (phân loại câu trích dẫn theo hướng hỗ trợ hay trái chiều), Consensus và Elicit (trợ lý tìm kiếm và tổng hợp tài liệu khoa học bằng AI), các công cụ phát hiện tài liệu tham khảo bịa đặt, và các trình quản lý tài liệu tham khảo. Mô tả này dựa trên hiểu biết chung, chưa được kiểm chứng; tính năng và tình trạng hiện tại của từng công cụ sẽ được xác minh ở Phase 1.

**Cập nhật 20/11/2026:** đã tra cứu sơ bộ, kết quả ở `02` §4.3. Kiểm tra tài liệu tham khảo bịa đặt đã có nhiều công cụ mã nguồn mở, nên khác biệt phải đến từ phần citation fidelity và hỗ trợ tiếng Việt.

Các điểm khác biệt cần kiểm chứng `[GĐ]`:

1. Kiểm tra theo từng bài viết của chính người dùng, thay vì là công cụ tra cứu tài liệu.
2. Hỗ trợ tiếng Việt và kiểm chứng xuyên ngôn ngữ.
3. Trung thực về trạng thái truy cập nguồn.

### 4.8 Giả định cần kiểm chứng

| ID | Giả định | Cách kiểm chứng ở Phase 1 | Ảnh hưởng nếu sai |
|---|---|---|---|
| A-01 | Người dùng gặp tình trạng trích dẫn không hỗ trợ nhận định hoặc sai metadata đủ thường xuyên để cần công cụ | Phỏng vấn ngắn sinh viên; xem mẫu bài thực tế | Cao |
| A-02 | Phần lớn tài liệu được trích trong bài CS–AI có toàn văn mở | Lấy mẫu bài thực tế, thống kê tỷ lệ reference có toàn văn mở | Cao |
| A-03 | Người dùng có sẵn PDF tài liệu họ trích và sẵn lòng tải lên | Phỏng vấn | Trung bình |
| A-04 | Bài tiếng Việt ngành CS–AI chủ yếu trích tài liệu tiếng Anh | Dùng cùng mẫu với A-02 | Trung bình |
| A-05 | LLM hiện có đủ khả năng đối chiếu claim tiếng Việt với bằng chứng tiếng Anh | Thử thủ công trên một số cặp claim–bằng chứng | Cao |
| A-06 | Có điểm khác biệt rõ so với giải pháp hiện có | Phân tích giải pháp hiện có | Cao |
| A-07 | Kiểm chứng citation fidelity xuyên ngôn ngữ có thể là đóng góp nghiên cứu | Literature review | Trung bình (ảnh hưởng phần nghiên cứu) |
| A-08 | Bạn đủ chuyên môn CS–AI để tự gán nhãn đáng tin cậy | Tự đánh giá; đo intra-annotator agreement trên mẫu thử | Cao |
| A-09 | Có thể tiếp cận một nhóm sinh viên để pilot | Liệt kê kênh tiếp cận cụ thể | Trung bình |

### 4.9 Câu hỏi mở

| ID | Câu hỏi | Chốt ở |
|---|---|---|
| Q-01 | Ngân sách cho LLM API (theo tháng hoặc tổng cho MVP) | Phase 1 |
| Q-02 | Tech stack v1.0 đã chốt hay còn mở (liên quan P-03) | Phase 0 |
| Q-03 | Có được phép gửi bản thảo chưa công bố của người dùng tới LLM thương mại không; cần thông báo gì cho người dùng | Phase 1, Phase 3 |
| Q-04 | Có thu hẹp CS–AI về ML/NLP không | Phase 1 |
| Q-05 | Kênh công bố mục tiêu (hội nghị, workshop, tạp chí; trong nước hay quốc tế) và hạn nộp | Phase 1 |
| Q-06 | Có hạn cứng nào không (bảo vệ, nộp bài) | Phase 1 |
| Q-07 | Chiến lược đánh giá cụ thể (§4.5.3) | Phase 1 |
| Q-08 | Có dùng open-weight model trong MVP không | Phase 3 |
| Q-09 | Môi trường chạy pilot (máy cá nhân hay cloud) | Phase 3 |
| Q-10 | Số giờ mỗi tuần bạn dành cho dự án (ảnh hưởng roadmap `11`) | Phase 1 |

## 5. Implementation Steps

Kế hoạch sơ bộ cho Phase 1 `[ĐX]`, chi tiết hóa khi bắt đầu phase:

| Bước | Việc làm | Giả định / câu hỏi liên quan |
|---|---|---|
| 1 | Literature review: citation fidelity, scientific claim verification, cross-lingual verification, dataset có sẵn | A-07, E4 |
| 2 | Phân tích giải pháp hiện có | A-06 |
| 3 | Lấy mẫu bài CS–AI thực tế (tiếng Việt và tiếng Anh), thống kê loại tài liệu tham khảo và tỷ lệ có toàn văn mở | A-02, A-04 |
| 4 | Phỏng vấn ngắn sinh viên, và giảng viên nếu có thể | A-01, A-03, A-09 |
| 5 | Thử thủ công khả năng đối chiếu claim tiếng Việt với bằng chứng tiếng Anh | A-05 |
| 6 | Gán nhãn thử một mẫu nhỏ, đo thời gian và độ nhất quán | A-08, kích thước tập đánh giá |
| 7 | Trả lời các câu hỏi mở chốt ở Phase 1 | Q-01, Q-03 đến Q-07, Q-10 |

Bước 3 và bước 6 nên dùng chung một mẫu bài để tiết kiệm thời gian.

## 6. Expected Outputs

- Phân tích được thống nhất về điểm mạnh, vấn đề và mâu thuẫn của v1.0.
- Danh sách giả định A-01 đến A-09 và câu hỏi mở Q-01 đến Q-10, mỗi mục có phase xử lý.
- Phương án chiến lược đánh giá cho điều kiện một người gán nhãn.
- Kế hoạch sơ bộ cho Phase 1.

## 7. Dependencies

| Phụ thuộc | Mô tả |
|---|---|
| Tài liệu v1.0 | Đối tượng được phân tích |
| `00-project-overview.md` | Decision Log và phạm vi MVP |
| Phase 1 | Sử dụng §4.8, §4.9 và §5 làm đầu vào |

## 8. Risks and Considerations

| ID | Rủi ro | Ảnh hưởng | Biện pháp giảm thiểu |
|---|---|---|---|
| R-01 | Trễ tiến độ do làm một mình | Cao | Vùng đệm thời gian (P-01); chỉ làm một milestone tại một thời điểm; cắt phạm vi trước khi kéo dài thời gian |
| R-02 | Phạm vi phình trở lại tầm nhìn v1.0 | Cao | Mọi bổ sung phạm vi phải qua Decision Log; giữ danh sách ngoài MVP |
| R-03 | Nhãn đánh giá thiếu độ tin cậy | Cao | Chiến lược ở §4.5.3; nêu rõ giới hạn khi công bố |
| R-04 | Tỷ lệ toàn văn mở thấp hơn kỳ vọng | Cao | Đo sớm (A-02); tải lên tài liệu được trích (D-08) |
| R-05 | Truy xuất xuyên ngôn ngữ cho kết quả kém | Trung bình | Thử sớm (A-05); so sánh phương án ở Phase 3 |
| R-06 | Báo nhầm tài liệu thật là không tồn tại | Cao | Bộ trạng thái reference ở §4.5.2; diễn đạt thận trọng với người dùng |
| R-07 | Parse tài liệu tham khảo sai do đa dạng định dạng trích dẫn và bảng mã | Trung bình | Bộ mẫu kiểm thử đa dạng; hiển thị chuỗi gốc khi không chắc chắn |
| R-08 | Quyền riêng tư của bản thảo chưa công bố | Trung bình | Q-03; thông báo rõ cho người dùng; chính sách lưu trữ và xóa dữ liệu |
| R-09 | Chi phí LLM vượt ngân sách | Trung bình | Q-01; cache kết quả; chọn model theo tác vụ |
| R-10 | Không đủ khác biệt so với giải pháp hiện có | Cao | A-06; điều chỉnh định vị sau Phase 1 |

## 9. Acceptance Criteria

- [ ] Mọi vấn đề chiến lược (§4.3) và mâu thuẫn (§4.4) có trạng thái và phase xử lý.
- [ ] Mọi giả định (§4.8) có cách kiểm chứng cụ thể.
- [ ] Mọi câu hỏi mở (§4.9) có phase chốt.
- [ ] Phân tích không mâu thuẫn với Decision Log ở `00` §4.4.
- [ ] Không có số liệu hay nguồn tham khảo nào được đưa ra mà chưa kiểm chứng; các mô tả về công cụ bên ngoài được đánh dấu là sơ bộ.

## 10. References

- CiteProof AI Project Documentation v1.0.
- Master Prompt: Project Initialization & Development Workflow.
- Ba vòng hỏi đáp Phase 0, ngày 20/11/2026.
- Các công cụ và nguồn dữ liệu bên ngoài được nhắc đến (Crossref, OpenAlex, scite.ai, Consensus, Elicit) chỉ mang tính định danh; thông tin chi tiết sẽ được kiểm chứng và trích nguồn ở Phase 1.
