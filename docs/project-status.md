# Project Status — CiteProof AI

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 27/11/2026 |
| Phase hiện tại | Phase 6 — Implementation |
| Trạng thái phase | IN_PROGRESS (MVP mã trên `main`; nhánh `feat/multi-provider-completion` thêm nhiều provider LLM và hoàn tất việc mã còn lại; việc còn mở là nghiên cứu, dữ liệu, pilot, chạy LLM thật) |
| Milestone hiện tại | Chưa áp dụng (milestone bắt đầu từ M0 trong `11`) |

## Tiến độ theo phase

| Phase | Tên | Tài liệu | Trạng thái phase | Trạng thái tài liệu |
|---|---|---|---|---|
| 0 | Project Initialization | `00`, `01` | COMPLETED (ngầm định) | Tài liệu đã dùng làm nền cho mã; P0-07…P0-09 vẫn chờ bạn xác nhận chính thức |
| 1 | Idea Validation & Research | `02`, `03` | IN_PROGRESS | Desk research sơ bộ có; M0 (T-001…T-010) chưa làm |
| 2 | Requirements Engineering | `04` | COMPLETED (ngầm định) | PRD đã được dùng để cài đặt; cập nhật theo ADR-011 |
| 3 | System Architecture & Technical Design | `07`, `07a`, `08`, `09`, `10` | COMPLETED (ngầm định) | Đã cài đặt; ADR-011 (nhiều provider) |
| 4 | Project Planning | `11`, `task-tracking.md` | IN_PROGRESS | Theo dõi ở `task-tracking.md` |
| 5 | Development Preparation | `12`, `13` | COMPLETED (ngầm định) | CI, quy ước, chiến lược test đang áp dụng |
| 6 | Implementation | Mã nguồn | IN_PROGRESS | Mã hoàn tất (T-604 xong); còn T-606 vận hành và các task chờ dữ liệu (`task-tracking.md`) |
| 7 | Testing & Quality Assurance | `13` | IN_PROGRESS | 1856 test tự động, coverage 96%; thực nghiệm T-7xx chưa chạy |
| 8 | Deployment & DevOps | `14` | IN_PROGRESS | Có `docker-compose.prod.yml`, backup, smoke; T-606 chưa chạy thật |
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

1. Cấu hình một provider LLM rồi chạy `docs/live-check.md` (b), (d); ghi chi phí thật (chưa chạy: chưa cấu hình LLM).
2. Chốt Q-03 (tên và link chính sách dữ liệu nhà cung cấp LLM trên màn hình đồng ý).
3. Chọn máy chủ và tên miền, làm T-606; sau đó Pilot (T-306, T-607).
4. Làm M0 (T-001…T-010) và dữ liệu đánh giá (T-E01…T-E07), rồi thực nghiệm T-701…T-705.
5. Quyết định số phận nhánh `feat/core-pipeline` và trả lời Q-10.

## Lịch sử cập nhật

| Ngày | Thay đổi |
|---|---|
| 20/11/2026 | Khởi tạo; Phase 0 chuyển sang IN_REVIEW |
| 20/11/2026 | Soạn trước toàn bộ tài liệu Phase 1–9 theo yêu cầu; desk research sơ bộ cho `02`; cập nhật NC-2 (`00`), thêm Q-10 (`01`) |
| 23/11/2026 | Thiết kế điều phối, bộ nhớ, công cụ LLM (`07a`) được duyệt (D-11, D-12, D-13); cập nhật `04`, `07`–`13`, task-tracking; ghi ADR-009, ADR-010. Bắt đầu lập kế hoạch và viết mã M1 theo yêu cầu, song song với Phase 1 còn mở |
| 23/11/2026 | M1 nền tảng hoàn tất trên nhánh feat/m1-foundation (T-101…T-109) |
| 24/11/2026 | Giao diện web v1 (ADR-005; `.stitch/DESIGN.md`, `.stitch/SITE.md`) hoàn tất trên nhánh feat/web-ui: đăng nhập, đồng ý, tải lên, tiến độ, báo cáo 4 phần, phản hồi, xóa dữ liệu, bài mẫu, chế độ thử. T-305 kéo lên làm trước |
| 27/11/2026 | MVP mã hoàn tất trên `main`; phase hiện tại chuyển sang Phase 6; Compose nạp `.env`; làm mới README và task-tracking |
| 27/11/2026 | Nhánh `feat/multi-provider-completion`: nhiều provider LLM, xuất HTML in được (T-604), sửa việc hoãn P1, coverage trong CI, hướng dẫn kiểm tra live (`docs/live-check.md`), audit §6. GROBID live đạt; LLM live chưa chạy: chưa cấu hình LLM |
