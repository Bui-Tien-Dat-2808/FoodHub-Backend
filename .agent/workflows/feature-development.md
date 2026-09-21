# Workflow: Feature Development

Dùng khi nhận task "thêm tính năng X".

## Các bước

1. **Làm rõ yêu cầu**
   - Đọc lại yêu cầu, xác định input/output mong muốn, ràng buộc, edge case.
   - Nếu mơ hồ ở điểm quan trọng (ảnh hưởng thiết kế), hỏi; nếu chi tiết nhỏ, chọn
     phương án hợp lý và ghi rõ giả định.

2. **Khảo sát codebase**
   - Tìm code liên quan đã tồn tại (tránh viết trùng).
   - Xác định pattern/kiến trúc hiện có để tuân theo (không tự sáng tạo pattern mới
     nếu không cần thiết).

3. **Thiết kế ngắn gọn**
   - Với task nhỏ: nêu trong 2-3 câu cách tiếp cận trước khi code.
   - Với task lớn: liệt kê các file sẽ tạo/sửa, luồng dữ liệu chính.

4. **Implement**
   - Viết code theo `rules/01-coding-standards.md`.
   - Viết song song hoặc ngay sau đó: test (`skills/testing.md`).

5. **Tự kiểm tra**
   - Chạy test, lint, build.
   - Tự review diff (`skills/code-review.md`).

6. **Báo cáo kết quả**
   - Tóm tắt: đã thêm/sửa gì, đã test ra sao, có giới hạn/việc chưa làm gì không.
   - Nếu cần, đề xuất bước tiếp theo (deploy, thêm test, tài liệu).
