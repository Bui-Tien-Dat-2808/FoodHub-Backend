# Rule: Coding Standards

> Quy chuẩn này phù hợp với stack hiện tại của project: backend FastAPI + SQLAlchemy + PostgreSQL, frontend React + TypeScript + Vite, và test bằng pytest + Vitest/Build check.

## Nguyên tắc chung
- Ưu tiên code dễ đọc và dễ bảo trì hơn là viết ngắn gọn nhưng khó hiểu.
- Đặt tên biến, hàm, component và API route theo ý nghĩa rõ ràng; tránh viết tắt khó hiểu.
- Hàm/component nên làm một việc rõ ràng; nếu vượt quá khoảng 40–50 dòng, cân nhắc tách nhỏ.
- Không để code chết, comment thừa, hoặc `console.log`/`print` debug sót lại trong code chính thức.
- Xử lý lỗi rõ ràng: không nuốt exception âm thầm (`except: pass`, `catch {}` rỗng) và luôn trả về thông điệp phù hợp cho user hoặc log.

## Style & format
- Tuân thủ formatter/linter đã cấu hình trong repo: Python code nên giữ style theo chuẩn trong backend, frontend nên dùng cấu trúc TypeScript/React nhất quán với code xung quanh.
- Giữ style thống nhất với các file hiện có, kể cả khi không phải phong cách bạn thích nhất.
- Với React/TypeScript: ưu tiên kiểu rõ ràng, tránh `any` nếu có thể, và giữ component nhỏ, dễ đọc.
- Với FastAPI/SQLAlchemy: ưu tiên model/schema/service rõ ràng, không lẫn logic business vào router quá nhiều.

## Test
- Mọi tính năng mới hoặc bugfix cần đi kèm test tương ứng.
- Backend: ưu tiên test bằng pytest cho service/API logic.
- Frontend: sau thay đổi UI/logic, cần chạy build/check để đảm bảo không lỗi compile.
- Test phải chạy độc lập, không phụ thuộc thứ tự chạy hay dữ liệu ngoài.
- Không sửa test chỉ để test pass mà không sửa nguyên nhân gốc — trừ khi test sai.

## Dependencies
- Không thêm thư viện mới nếu có thể giải quyết bằng code ngắn gọn hoặc thư viện đã có sẵn trong stack.
- Khi thêm dependency mới, cần giải thích rõ lý do và ảnh hưởng tới project.
- Với frontend, ưu tiên dùng thư viện đã có trong project thay vì thêm package mới không cần thiết.

## Bảo mật cơ bản
- Không hardcode secret, API key, token hoặc thông tin nhạy cảm trong code.
- Validate input ở boundary: API request, form input, query params, và dữ liệu từ client.
- Không dùng string concatenation để xây dựng SQL query; nên dùng parameterized query hoặc ORM query phù hợp.
- Khi làm việc với auth, JWT, session hoặc dữ liệu user, luôn giữ nguyên tắc an toàn và không lộ thông tin nhạy cảm.
