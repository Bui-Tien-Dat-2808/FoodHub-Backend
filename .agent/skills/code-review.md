# Skill: Code Review

## Khi nào dùng
Trước khi coi một thay đổi là "hoàn thành", hoặc khi được yêu cầu review code
(của mình hoặc người khác).

## Checklist
1. **Đúng chức năng**: code có làm đúng điều task yêu cầu, kể cả edge case?
2. **Tính đúng đắn**: có off-by-one, null/undefined chưa xử lý, race condition không?
3. **Test**: có test cho logic mới? Test có thực sự kiểm chứng hành vi, hay chỉ chạy
   cho có?
4. **Bảo mật**: input từ người dùng có được validate? Có lộ secret không?
5. **Hiệu năng**: có vòng lặp lồng nhau không cần thiết, query N+1, hay load toàn bộ
   dữ liệu vào memory một cách lãng phí?
6. **Khả năng đọc**: người khác (hoặc chính bạn 6 tháng sau) có hiểu code này không
   cần hỏi lại?
7. **Phạm vi**: thay đổi có nằm đúng phạm vi task, không lẫn thay đổi không liên quan?

## Cách phản hồi
- Phân loại rõ: **phải sửa** (blocking) vs **gợi ý** (nice-to-have).
- Chỉ ra dòng/file cụ thể, kèm lý do — không chỉ nói "chỗ này chưa ổn".
- Nếu code ổn, nói rõ là ổn — không tạo phản hồi giả cho có.
