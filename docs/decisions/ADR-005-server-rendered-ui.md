# ADR-005: Giao diện render phía server cho pilot

| Thuộc tính | Giá trị |
|---|---|
| Trạng thái | Accepted |
| Ngày | 2026-11-24 |
| Liên quan | NFR-09, NFR-10, `07` §3, `09` §1, spec `superpowers/specs/2026-11-24-web-ui-design.md` |

## Context

- Dự án có một người bảo trì (NFR-10). Toàn bộ backend viết bằng Python.
- Người dùng chính (chủ dự án) muốn thực nghiệm ngay trên giao diện thật, rồi dùng chính giao diện đó cho pilot.
- Giao diện cần: đăng nhập, đồng ý dữ liệu, tải lên, tiến độ tự cập nhật, báo cáo 4 phần, tờ đối chiếu, phản hồi, xóa dữ liệu.

## Problem

Xây giao diện pilot bằng công nghệ nào?

## Alternatives

| Cách | Đánh đổi |
|---|---|
| A. FastAPI + Jinja2 + HTMX trong service hiện có | Một service, toàn Python, CSS thuần, không bước build. Tương tác bằng thay mảnh HTML |
| B. React + Vite + shadcn/ui tách riêng | Mượt hơn, nhiều component sẵn; thêm codebase TypeScript, bước build, cơ chế token; gấp đôi công bảo trì |
| C. Streamlit | Nhanh nhất để xem dữ liệu; không làm được đăng nhập, đồng ý, tách dữ liệu theo người dùng |

## Decision

Chọn A. Route web gọi thẳng tầng dịch vụ (`citeproof.services`); view model dùng tên trường của `09` §4.4 để sau này dựng API JSON và, nếu cần, frontend riêng trên cùng tầng dịch vụ. HTMX và font được tự phục vụ; CSP chặn script và style nội tuyến. Phong cách thị giác theo `.stitch/DESIGN.md`.

## Consequences

- Một service, một ngôn ngữ, test route bằng `httpx` không cần trình duyệt.
- Tương tác phức tạp (tờ đối chiếu) dùng thay mảnh HTML; trang vẫn dùng được khi không có JavaScript, trừ tự cập nhật tiến độ (có liên kết "Làm mới").
- Next.js hoặc frontend riêng để sau MVP, khi có nhu cầu thật.
