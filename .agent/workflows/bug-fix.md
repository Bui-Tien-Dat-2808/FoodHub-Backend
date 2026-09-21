# Workflow: Bug Fix

Dùng khi nhận báo cáo lỗi hoặc test đang fail.

## Các bước

1. **Xác nhận lỗi** — tái hiện được lỗi (chạy test fail, hoặc thực hiện đúng bước
   người dùng mô tả). Nếu không tái hiện được, nói rõ và hỏi thêm thông tin
   (log, phiên bản, bước tái hiện) thay vì đoán mò.

2. **Debug** — theo `skills/debugging.md`, tìm nguyên nhân gốc, không vá triệu chứng.

3. **Viết test tái hiện lỗi** (nếu chưa có) — test này phải fail trước khi fix, và
   pass sau khi fix.

4. **Fix** — thay đổi tối thiểu để giải quyết nguyên nhân gốc, tuân theo
   `rules/01-coding-standards.md`.

5. **Kiểm tra hồi quy (regression)** — chạy toàn bộ test suite liên quan để đảm bảo
   fix không phá vỡ chỗ khác.

6. **Báo cáo**
   - Nguyên nhân gốc là gì.
   - Đã sửa như thế nào, ở file nào.
   - Test nào chứng minh đã fix.
   - (Nếu liên quan) Có cần theo dõi thêm ở production không.
