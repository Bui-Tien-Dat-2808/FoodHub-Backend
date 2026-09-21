# Skill: Refactoring

## Khi nào dùng
Khi cải thiện cấu trúc code mà không đổi hành vi bên ngoài (behavior-preserving).

## Nguyên tắc
1. **Có test bảo vệ trước khi refactor** — nếu chưa có test cho phần sắp đổi, viết
   test characterization trước để đảm bảo hành vi hiện tại không bị phá vỡ.
2. **Từng bước nhỏ** — mỗi bước refactor nên là một thay đổi có thể revert độc lập,
   chạy test sau mỗi bước thay vì đổi hàng loạt rồi mới test.
3. **Không trộn refactor với thay đổi tính năng** trong cùng một commit/PR — tách
   riêng để dễ review và dễ revert nếu có vấn đề.
4. **Có lý do rõ ràng** — refactor để giải quyết vấn đề cụ thể (khó test, trùng lặp,
   khó mở rộng), không refactor chỉ vì "trông đẹp hơn".

## Các mùi code (code smell) thường gặp cần refactor
- Trùng lặp logic ở nhiều nơi (duplicate code).
- Hàm/class làm quá nhiều việc (low cohesion).
- Tham số quá nhiều hoặc tên mơ hồ.
- Nested if/else sâu — cân nhắc early return hoặc guard clause.
- Magic number/string lặp lại — nên đặt thành constant có tên.
