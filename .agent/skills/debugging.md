# Skill: Debugging

## Khi nào dùng
Khi có lỗi (test fail, exception, hành vi sai) cần tìm nguyên nhân gốc.

## Quy trình
1. **Tái hiện lỗi** — chạy lại để thấy lỗi thực sự xảy ra, đừng chỉ đọc code và đoán.
2. **Thu hẹp phạm vi** — dùng log/print/debugger để xác định lỗi xảy ra ở bước nào,
   không sửa đại trước khi biết chắc nguyên nhân.
3. **Đặt giả thuyết, kiểm tra từng giả thuyết một** — tránh sửa nhiều thứ cùng lúc rồi
   không biết cái nào thực sự fix được lỗi.
4. **Tìm nguyên nhân gốc, không chỉ vá triệu chứng** — ví dụ: nếu lỗi vì giá trị null,
   hỏi *tại sao* nó null, không chỉ thêm null-check để lỗi im lặng biến mất.
5. **Viết test tái hiện lỗi trước khi fix** (nếu có thể) — đảm bảo lỗi không quay lại.
6. **Xác nhận đã fix** — chạy lại test/case gốc, và chạy test suite liên quan để chắc
   không phá vỡ chỗ khác.

## Khi bế tắc
- Nêu rõ những gì đã thử và loại trừ được, không lặp lại cùng một hướng đã thất bại.
- Nếu nghi ngờ lỗi nằm ngoài phạm vi code đang xem (thư viện, môi trường, dữ liệu),
  nói rõ giả thuyết đó thay vì im lặng đoán tiếp.
