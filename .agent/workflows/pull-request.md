# Workflow: Pull Request

Dùng khi thay đổi đã sẵn sàng để đưa lên review.

## Các bước

1. **Tự review lần cuối** — `git diff` toàn bộ thay đổi, áp checklist trong
   `skills/code-review.md`.

2. **Dọn dẹp**
   - Xoá code debug, comment thừa, file tạm không liên quan.
   - Squash commit lộn xộn nếu cần (giữ lịch sử commit có ý nghĩa).

3. **Tạo branch & commit** theo `rules/03-git-conventions.md`.
   - **Không tự push lên remote hoặc tạo PR thật nếu chưa được người dùng xác nhận**
     (xem `rules/02-safety-guardrails.md`).

4. **Viết PR description**
   ```
   ## Vấn đề
   [Mô tả ngắn vấn đề/tính năng]

   ## Giải pháp
   [Cách tiếp cận, lý do chọn cách này]

   ## Đã test
   [Test nào đã chạy, kết quả]

   ## Ghi chú
   [Rủi ro, việc chưa làm, điểm cần reviewer chú ý]
   ```

5. **Đề xuất reviewer/label** nếu framework/quy trình dự án yêu cầu.

6. **Sau khi có feedback** — xử lý từng comment, không im lặng bỏ qua; nếu không đồng
   ý với góp ý, giải thích lý do thay vì âm thầm sửa hoặc âm thầm phớt lờ.
