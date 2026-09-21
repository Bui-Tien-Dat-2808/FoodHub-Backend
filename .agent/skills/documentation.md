# Skill: Documentation

## Khi nào dùng
Khi thêm tính năng mới, thay đổi API/behavior công khai, hoặc khi code khó hiểu nếu
không có giải thích.

## Nguyên tắc
- Comment giải thích **tại sao** (why), không lặp lại **cái gì** (what) đã rõ từ code.
- Docstring/JSDoc cho public function: mô tả mục đích, tham số, giá trị trả về,
  trường hợp ném lỗi (nếu có).
- README/CHANGELOG cập nhật khi thay đổi ảnh hưởng người dùng cuối hoặc dev khác
  (breaking change, API mới, cách cài đặt thay đổi).
- Không viết tài liệu thừa cho code tự giải thích (self-explanatory) — ưu tiên đặt
  tên rõ hơn là thêm comment.

## Khi viết README cho dự án/module mới
Tối thiểu gồm: mục đích, cách cài đặt, cách chạy, cách chạy test, cấu trúc thư mục
chính. Ngắn gọn, đúng thực tế — không copy template chung chung không khớp dự án.
