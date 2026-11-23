# CiteProof AI — Bộ tài liệu dự án

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 20/11/2026 |
| Phase hiện tại | Phase 0 (IN_REVIEW) |

## Cách dùng bộ tài liệu

Toàn bộ tài liệu từ Phase 0 đến Phase 9 được soạn sẵn theo yêu cầu ngày 20/11/2026. Tài liệu Phase 0 đã qua ba vòng hỏi đáp. Tài liệu các phase sau là **bản nháp trước (pre-draft)**: chúng dựa trên Decision Log hiện có. Mọi nội dung còn phụ thuộc vào kết quả Phase 1 hoặc vào quyết định của bạn đều được đánh dấu `[GĐ]` hoặc `[ĐX]`.

Quy tắc khi dùng:

1. Làm theo thứ tự phase. Khi bắt đầu một phase, rà soát tài liệu của phase đó và cập nhật theo kết quả của các phase trước.
2. Phase gate vẫn giữ nguyên: một phase chỉ hoàn tất khi đạt Acceptance Criteria trong tài liệu tương ứng.
3. Khi kết quả một phase làm thay đổi giả định, cập nhật mọi tài liệu bị ảnh hưởng và ghi lại vào Decision Log (`00` §4.4) hoặc ADR.

## Danh mục tài liệu

| File | Phase | Nội dung chính | Trạng thái |
|---|---|---|---|
| `00-project-overview.md` | 0 | Định nghĩa MVP, phạm vi, Decision Log | IN_REVIEW |
| `01-idea-analysis.md` | 0 | Phân tích ý tưởng, mâu thuẫn v1.0, giả định, câu hỏi mở | IN_REVIEW |
| `02-market-user-research.md` | 1 | Giải pháp hiện có và nghiên cứu liên quan (đã tra cứu sơ bộ), persona, kế hoạch phỏng vấn | Bản nháp trước |
| `03-feasibility-study.md` | 1 | Khả thi kỹ thuật, dữ liệu, nghiên cứu, nguồn lực, pháp lý; tiêu chí go/no-go | Bản nháp trước |
| `04-prd.md` | 2 | Yêu cầu chức năng và phi chức năng, user stories, use cases | Bản nháp trước |
| `07-technical-architecture.md` | 3 | Kiến trúc, pipeline, thiết kế LLM, đánh giá lại tech stack | Bản nháp trước |
| `07a-orchestration-memory-tools.md` | 3 | Điều phối (worker, agent có giới hạn), bộ nhớ 5 lớp, bộ công cụ và schema, ngân sách context | Đã duyệt (D-13) |
| `decisions/ADR-009…`, `ADR-010…` | 3 | Agent có giới hạn trong pipeline; hiện thực vòng lặp agent | ADR-009 Accepted, ADR-010 Proposed |
| `08-database-design.md` | 3 | Lược đồ dữ liệu MVP | Bản nháp trước |
| `09-api-interface-specification.md` | 3 | REST API và interface nội bộ | Bản nháp trước |
| `10-security-risk-assessment.md` | 3 | Bảo mật, quyền riêng tư, bản quyền, sổ rủi ro bảo mật | Bản nháp trước |
| `11-development-roadmap.md` | 4 | Milestone, epic, phụ thuộc, Definition of Done | Bản nháp trước |
| `progress/task-tracking.md` | 4 | Danh sách task và mẫu mô tả task | Bản nháp trước |
| `12-development-guidelines.md` | 5 | Cấu trúc mã nguồn, quy ước code, Git, logging, xử lý lỗi | Bản nháp trước |
| `13-testing-strategy.md` | 5, 7 | Kiểm thử phần mềm và đánh giá khoa học | Bản nháp trước |
| `14-deployment-devops.md` | 8 | Triển khai môi trường pilot | Bản nháp trước |
| `15-monitoring-maintenance.md` | 9 | Giám sát, bảo trì, nâng cấp model | Bản nháp trước |
| `decisions/ADR-template.md` | 3 trở đi | Mẫu ADR và danh sách ADR đề xuất | Mẫu |
| `project-status.md` | Tất cả | Tiến độ dự án | Cập nhật liên tục |

Theo D-09, `05` và `06` được gộp vào `04-prd.md`.

## Quy ước chung

| Ký hiệu / mã | Ý nghĩa | Nơi định nghĩa |
|---|---|---|
| `[XN]` `[GĐ]` `[ĐX]` | Đã xác nhận / Giả định / Đề xuất | `00` |
| D-xx, P-xx | Quyết định đã xác nhận, đề xuất chờ xác nhận | `00` §4.4 |
| A-xx, Q-xx | Giả định, câu hỏi mở | `01` §4.8, §4.9 |
| R-xx | Rủi ro dự án | `01` §8 |
| SPK-xx | Spike (thử nghiệm kỹ thuật ngắn) | `03` §4.1 |
| FR-xx, NFR-xx, US-xx, UC-xx | Yêu cầu, user story, use case | `04` |
| ADR-xxx | Quyết định kiến trúc | `decisions/` |
| SR-xx | Rủi ro bảo mật | `10` |
| M0–M9, T-xxx | Milestone, task | `11`, `progress/task-tracking.md` |

Trạng thái công việc: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `IN_REVIEW`, `COMPLETED`.

## Cấu trúc thư mục

```text
docs/
├── README.md
├── 00-project-overview.md … 15-monitoring-maintenance.md
├── project-status.md
├── reference/
│   └── citeproof-v1.0.md          # Tài liệu gốc v1.0, giữ nguyên để đối chiếu
├── decisions/
│   └── ADR-template.md            # ADR-001, ADR-002… tạo khi chốt quyết định
└── progress/
    └── task-tracking.md
```
