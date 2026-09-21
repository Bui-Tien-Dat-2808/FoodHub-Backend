# Rule: Git Conventions

## Nhánh (branch)
- Đặt tên: `feat/<mô-tả-ngắn>`, `fix/<mô-tả-ngắn>`, `chore/<mô-tả-ngắn>`.
- Không làm việc trực tiếp trên `main`/`master`.

## Commit message
Theo chuẩn Conventional Commits:
```
<type>(<phạm-vi>): <mô tả ngắn, thì hiện tại>

[phần thân, giải thích lý do nếu cần]
```
Các `type` phổ biến: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`.

Ví dụ:
```
fix(auth): sửa lỗi token hết hạn không tự refresh

Token refresh trước đó chỉ chạy khi request thất bại lần đầu,
dẫn tới race condition khi nhiều request đồng thời. Thêm mutex
lock quanh bước refresh.
```

## Pull Request
- Mô tả: **Vấn đề gì** → **Giải pháp** → **Cách đã test**.
- Diff nhỏ, tập trung một mục đích; PR lớn nên tách nhỏ nếu có thể.
- Tự review lại diff của chính mình trước khi coi là "sẵn sàng" (xem
  `skills/code-review.md`).
