# Skill: Testing

## Khi nào dùng
Khi viết tính năng mới, sửa bug, hoặc refactor — bất cứ khi nào code hành vi thay đổi.

## Nguyên tắc viết test
- Test tên rõ ràng, mô tả *hành vi* được kiểm tra (không phải tên hàm suông):
  `test_refresh_token_khi_het_han` tốt hơn `test_1`.
- Một test kiểm tra một hành vi. Tránh test "khổng lồ" kiểm tra nhiều thứ cùng lúc.
- Bao gồm cả: trường hợp bình thường (happy path), trường hợp biên (edge case),
  và trường hợp lỗi (error case).
- Mock/stub phụ thuộc ngoài (network, DB, thời gian hệ thống) — test không nên gọi
  API thật hay phụ thuộc mạng khi chạy trong CI.
- Test phải deterministic — chạy lại nhiều lần phải ra cùng kết quả.

## Loại test theo tầng
- **Unit test**: logic thuần, cô lập, chạy nhanh — nên chiếm phần lớn.
- **Integration test**: kiểm tra các phần ghép với nhau (DB thật/test container, v.v).
- **E2E test**: luồng người dùng đầu-cuối — ít nhưng quan trọng cho critical path.

## Trước khi báo "đã xong"
- Chạy toàn bộ test suite liên quan, không chỉ test mới viết.
- Kiểm tra coverage cho phần code mới có hợp lý, không chỉ chăm chăm đạt % coverage.
