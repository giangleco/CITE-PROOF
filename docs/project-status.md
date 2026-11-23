# Project Status — CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 20/11/2026 |
| Phase hiện tại | Phase 0 — Project Initialization |
| Trạng thái phase | IN_REVIEW |
| Milestone hiện tại | Chưa áp dụng (milestone bắt đầu từ M0 trong `11`) |

## Tiến độ theo phase

| Phase | Tên | Tài liệu | Trạng thái phase | Trạng thái tài liệu |
|---|---|---|---|---|
| 0 | Project Initialization | `00`, `01` | IN_REVIEW | Hoàn chỉnh, chờ xác nhận |
| 1 | Idea Validation & Research | `02`, `03` | NOT_STARTED | Bản nháp trước; `02` đã có desk research sơ bộ |
| 2 | Requirements Engineering | `04` | NOT_STARTED | Bản nháp trước |
| 3 | System Architecture & Technical Design | `07`, `08`, `09`, `10` | NOT_STARTED | Bản nháp trước |
| 4 | Project Planning | `11`, `progress/task-tracking.md` | NOT_STARTED | Bản nháp trước |
| 5 | Development Preparation | `12`, `13` | NOT_STARTED | Bản nháp trước |
| 6 | Implementation | Mã nguồn | NOT_STARTED | — |
| 7 | Testing & Quality Assurance | `13` §A.6 (mẫu báo cáo) | NOT_STARTED | — |
| 8 | Deployment & DevOps | `14` | NOT_STARTED | Bản nháp trước |
| 9 | Monitoring & Maintenance | `15` | NOT_STARTED | Bản nháp trước |

"Bản nháp trước" nghĩa là tài liệu đã soạn sẵn theo yêu cầu ngày 20/11/2026 và phải được rà soát, cập nhật khi đến phase tương ứng.

## Công việc Phase 0

| ID | Công việc | Trạng thái |
|---|---|---|
| P0-01 | Phân tích tài liệu v1.0 | COMPLETED |
| P0-02 | Vòng hỏi 1: mục tiêu và nguồn lực | COMPLETED |
| P0-03 | Vòng hỏi 2: ngôn ngữ, loại tài liệu tham khảo | COMPLETED |
| P0-04 | Vòng hỏi 3: lĩnh vực, gán nhãn, phạm vi MVP | COMPLETED |
| P0-05 | Tạo `00-project-overview.md` | IN_REVIEW |
| P0-06 | Tạo `01-idea-analysis.md` | IN_REVIEW |
| P0-07 | Xác nhận đề xuất P-01, P-02, P-03 | NOT_STARTED (chờ bạn) |
| P0-08 | Lưu tài liệu v1.0 vào `docs/reference/citeproof-v1.0.md` | NOT_STARTED (chờ bạn) |
| P0-09 | Đánh giá Acceptance Criteria và hoàn tất Phase 0 | NOT_STARTED |

## Công việc bị chặn

Không có.

## Rủi ro hiện tại

Các rủi ro mức Cao (chi tiết ở `01-idea-analysis.md` §8): R-01 trễ tiến độ do làm một mình, R-02 phạm vi phình, R-03 nhãn đánh giá thiếu độ tin cậy, R-04 tỷ lệ toàn văn mở thấp hơn kỳ vọng, R-06 báo nhầm tài liệu thật là không tồn tại, R-10 không đủ khác biệt với giải pháp hiện có.

Phát hiện mới từ desk research (`02` §4.4): R-10 tăng mức độ chú ý, vì kiểm tra tài liệu tham khảo bịa đặt đã có nhiều công cụ mã nguồn mở, và đã có công trình 2026 làm citation fidelity bằng multi-agent.

## Hành động tiếp theo

1. Rà soát `00` và `01`; xác nhận P-01, P-02, P-03.
2. Gửi `COMPLETE PHASE` để đóng Phase 0.
3. Bắt đầu Phase 1 theo `02` §5 và `03` §5; điền ngưỡng go/no-go ở `03` §4.7 trước khi đo.
4. Trả lời Q-10 (số giờ mỗi tuần) để chốt ngày trong roadmap `11`.

## Lịch sử cập nhật

| Ngày | Thay đổi |
|---|---|
| 20/11/2026 | Khởi tạo; Phase 0 chuyển sang IN_REVIEW |
| 20/11/2026 | Soạn trước toàn bộ tài liệu Phase 1–9 theo yêu cầu; desk research sơ bộ cho `02`; cập nhật NC-2 (`00`), thêm Q-10 (`01`) |
| 23/11/2026 | Thiết kế điều phối, bộ nhớ, công cụ LLM (`07a`) được duyệt (D-11, D-12, D-13); cập nhật `04`, `07`–`13`, task-tracking; ghi ADR-009, ADR-010. Bắt đầu lập kế hoạch và viết mã M1 theo yêu cầu, song song với Phase 1 còn mở |
