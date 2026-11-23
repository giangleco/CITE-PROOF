# ADR-009: Agent có giới hạn trong pipeline tất định

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted |
| Ngày | 2026-11-23 |
| Liên quan | D-13, ADR-002, NFR-04, NFR-05, NFR-06, NFR-08, `01` S-4/S-5, `02` §4.4, `07a` |

## Context

- v1.0 mô tả 9 Agent. Phân tích ở `01` (S-4, S-5) cho thấy phần lớn trong số đó là bước tất định.
- CiteAudit (2026) đã dùng pipeline multi-agent cho đúng bài toán citation fidelity, nên kiến trúc multi-agent không còn là điểm mới (`02` §4.4).
- Dự án chỉ có một người làm, yêu cầu tái lập được kết quả (NFR-06) và kiểm soát chi phí (NFR-08).
- Theo skill `multi-agent-patterns`, tác tử con chỉ đáng dùng khi cần tách context. Mỗi lần phán quyết vốn chỉ chứa một claim và top-k đoạn.

## Problem

Ai quyết định bước tiếp theo trong CiteProof MVP, và có dùng agent ở đâu không?

## Alternatives

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| A. Pipeline tất định + 2 agent có giới hạn (A1 tra cứu reference khó, A2 điều tra bằng chứng) | Tái lập được; chi phí kiểm soát được; dễ debug; có biến thể agentic để so sánh | Phải viết thêm một vòng lặp agent nhỏ |
| B. Multi-agent đầy đủ với LLM điều phối | Linh hoạt; đúng tầm nhìn v1.0 | Tốn token khoảng 15 lần; khó tái lập; điều phối viên làm sai lệch thông tin qua nhiều lần truyền; trùng hướng CiteAudit |
| C. Pipeline thuần, không agent | Đơn giản và rẻ nhất | Reference bị gõ sai hoặc dịch tiêu đề luôn ở trạng thái chưa tìm thấy; thiếu biến thể agentic |

## Decision

Chọn phương án A:

- Pipeline S1–S9 tất định tiếp tục làm điều phối.
- LLM dùng theo hai hình thức: worker (W1–W5, một lời gọi, không có công cụ) và agent có giới hạn (A1, A2, công cụ chỉ đọc, ngân sách cứng).
- Agent chỉ đề xuất; trạng thái cuối cùng do quy tắc tất định gán.

## Rationale

- Agent chỉ có giá trị ở chỗ bước tiếp theo phụ thuộc vào kết quả bước trước. A1 và A2 đúng là hai chỗ đó.
- Các bước còn lại không cần agent.
- Chi tiết và ước tính chi phí ở `07a` §4.1, §4.8.5.

## Consequences

- **Tích cực:**
  - Giữ được NFR-04, NFR-06.
  - Mỗi agent có trace riêng, tối đa 8 bước.
  - Bài báo so sánh được "agent có giới hạn" với V2/V3 và với CiteAudit.
- **Đánh đổi:**
  - Thêm bảng `agent_runs` và vòng lặp agent (ADR-010).
  - Quy tắc "LLM không có công cụ" ở `07` §4.6 được nới cho agent, chỉ với công cụ chỉ đọc.
- **Việc tiếp theo:**
  - A1 chỉ làm nếu phân tích lỗi ở T-407 cho thấy đáng làm.
  - Chế độ `V2+A2` quyết định ở T-705.
  - Cả hai agent nằm đầu thứ tự cắt giảm (`11` §4.5).
