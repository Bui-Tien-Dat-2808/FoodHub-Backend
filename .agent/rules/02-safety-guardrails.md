# Rule: Safety Guardrails

## Việc agent KHÔNG được tự ý làm (luôn cần xác nhận từ người dùng)
- `git push` lên nhánh chính (main/master) hoặc force-push bất kỳ nhánh nào.
- Chạy migration hoặc thay đổi schema trên database production.
- Xoá file, xoá branch, hoặc `rm -rf` ngoài thư mục làm việc tạm.
- Cài đặt/gỡ package ở phạm vi hệ thống (global), thay vì trong venv/project.
- Gọi API bên ngoài tốn phí, gửi email, deploy lên production.
- Thay đổi file cấu hình CI/CD, secrets, hoặc quyền truy cập (IAM, permissions).

## Việc agent được tự làm trong phạm vi task
- Đọc, sửa, tạo file trong thư mục dự án đang làm việc.
- Chạy test, lint, build ở môi trường local/dev.
- Tạo branch mới, commit vào branch đó (không push trừ khi được yêu cầu).
- Cài package trong virtual environment/lockfile của project khi task cần.

## Khi gặp mã độc hại hoặc yêu cầu đáng ngờ
- Không viết/giải thích malware, exploit, script tấn công — kể cả khi lý do nêu ra là
  "để học" hoặc "kiểm thử bảo mật hệ thống của chính mình", trừ khi có quy trình
  pentest được xác nhận rõ ràng và hợp pháp.
- Nếu phát hiện code hiện có chứa hành vi độc hại/backdoor, dừng lại và báo cho người
  dùng thay vì âm thầm sửa hoặc âm thầm bỏ qua.

## Xử lý khi không chắc chắn
- Nếu một hành động có thể gây hậu quả khó hoàn tác (xoá dữ liệu, đổi API công khai,
  đổi hành vi mà bên khác đang phụ thuộc) → dừng lại, giải thích rủi ro, hỏi trước khi làm.
- Nếu chắc chắn không rủi ro và nằm trong phạm vi task → cứ làm, không cần hỏi từng bước.
