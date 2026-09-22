# FoodHub Backend — Hệ thống Đặt & Giao Đồ Ăn Chịu Tải Cao

Backend API cho nền tảng đặt và giao đồ ăn nhiều tác nhân (**Customer**, **Merchant**, **Driver**, **Admin**), được xây dựng để giải quyết bài toán nghiệp vụ phức tạp, đảm bảo tính đúng đắn tuyệt đối dưới tải đồng thời cao (**Zero Oversell**), hỗ trợ xử lý tác vụ nền và giao tiếp thời gian thực (**Realtime WebSocket**).

Dự án hiện thực trọn vẹn **5 Trụ cột Kỹ thuật Backend nâng cao**:
1. **Cơ sở dữ liệu nâng cao (PostgreSQL 16):** Transaction đa bảng ACID, Atomic SQL Update chống oversell, loại bỏ N+1 query (`selectinload`), Composite Index, Check Constraints.
2. **Redis Caching & Phân tán (Redis 7):** Cache-Aside với TTL và Invalidation tường minh, Idempotency Key 24h chống trừ tiền trùng, Sliding Window Rate Limiter, Ephemeral GPS tracking.
3. **Message Queue (Celery + Redis Broker):** Async-Sync ThreadPool bridge, Tác vụ trễ 15 phút tự động hủy đơn (idempotent), Retry Exponential Backoff + Jitter cho cổng thanh toán, Celery Beat định kỳ đối soát lúc 23:59.
4. **WebSocket Realtime (Dual-Layer + Redis Pub/Sub Backplane):** Xác thực JWT qua query param (code 1008), phân quyền kênh theo đơn hàng & nhà hàng, mở rộng đa tiến trình (multi-process horizontal scaling).
5. **Bảo mật & OWASP (FastAPI Security):** JWT access + refresh token rotation, mật khẩu bcrypt, RBAC ma trận quyền chặt chẽ, OWASP Security Headers middleware, Structured Audit Trail logging.

---

## 🛠️ Công nghệ Sử dụng (Tech Stack)

| Thành phần | Công nghệ chính | Ghi chú |
| :--- | :--- | :--- |
| **Web Framework** | **FastAPI** `0.111.x` (Async) | Pydantic v2, Uvicorn, ASGI Async endpoints |
| **Database & ORM** | **PostgreSQL 16** + **SQLAlchemy 2.0** | AsyncSession (`asyncpg`), Alembic migration ready |
| **Cache & Realtime** | **Redis 7** (`redis-py` async) | Cache-aside, Invalidation, Rate limit, Pub/Sub |
| **Message Queue** | **Celery 5.4** + **Celery Beat** | Redis Broker & Result Backend, delayed tasks |
| **Authentication** | **PyJWT** + **Passlib** (`bcrypt`) | Access token + Refresh token rotation, RBAC |
| **Testing** | **Pytest** + **HTTPX** + **pytest-asyncio** | 39 test cases (Coverage nghiệp vụ lõi > 80%) |
| **DevOps** | **Docker** & **Docker Compose** | Khởi chạy toàn bộ 5 dịch vụ chỉ với 1 lệnh |

---

## 🚀 Khởi chạy Nhanh (Quick Start)

### 1. Khởi chạy toàn bộ hệ thống bằng Docker Compose
Chỉ cần **1 câu lệnh duy nhất** để khởi động toàn bộ: API Server, PostgreSQL, Redis, Celery Worker và Celery Beat:

```bash
docker compose up --build -d
```

Các dịch vụ sẽ sẵn sàng tại:
* **FastAPI Swagger Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **ReDoc Documentation:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
* **Health Check Endpoint:** [http://localhost:8000/health](http://localhost:8000/health)

---

### 2. Khởi tạo Dữ liệu mẫu (Database Seeding)
Chạy script seed để tạo sẵn tài khoản Admin, Nhà hàng, Menu, Khách hàng, Tài xế và Vouchers:

* **Chạy trực tiếp trên máy (local):**
  ```bash
  python scripts/seed.py
  ```
* **Hoặc chạy thông qua Docker:**
  ```bash
  docker compose exec api python scripts/seed.py
  ```

---

### 3. Kiểm thử Tải Đồng thời (Concurrency Load Test)
Chạy script mô phỏng **25 khách hàng** cùng lúc gửi request tại cùng 1 mili-giây để tranh mua món chỉ còn **5 suất tồn kho**:

```bash
python scripts/load_test.py
```

**Kết quả kiểm chứng (Zero Oversell):**
```text
=================================================================
CONCURRENCY TEST REPORT (BAO CAO KIEM THU DONG THOI)
=================================================================
 - Tong so request gui di:        25
 - Tong thoi gian xu ly:          380 ms
 - Don dat thanh cong (201):      5 don
 - Don bi tu choi het hang (400): 20 don
 - Ton kho ban dau:               5 suat
 - Ton kho thuc te con lai:       0 suat
-----------------------------------------------------------------
>> KET LUAN: [PASSED] ZERO OVERSELL!
   He thong ban dung chinh xac 5/5 suat.
   Khong bi am kho hay ban thua du nhan tai dong thoi cao.
=================================================================
```

---

### 4. Chạy Toàn bộ Test Suite
Chạy toàn bộ 39 bài test tự động (đạt 100% Green):

```bash
pytest -v
```

Kiểm tra định dạng và chuẩn linter:
```bash
ruff check .
```

---

## 🔑 Tài khoản Mẫu để Kiểm thử (Seeded Accounts)

| Vai trò | Email đăng nhập | Mật khẩu | Quyền hạn & Mô tả |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin@foodhub.com` | `admin123` | Quản trị toàn hệ thống, xem mọi đơn, hoàn tiền, audit logs |
| **Merchant** | `merchant_pho@foodhub.com` | `merchant123` | Chủ quán *Phở Bát Đàn*, quản lý menu, duyệt đơn, xem báo cáo |
| **Merchant** | `merchant_comtam@foodhub.com` | `merchant123` | Chủ quán *Cơm Tấm Sài Gòn*, có món Flash-Sale tồn kho = 5 |
| **Customer** | `customer1@foodhub.com` | `customer123` | Đặt đơn, áp voucher, theo dõi đơn qua WebSocket |
| **Customer** | `customer2@foodhub.com` $\rightarrow$ `customer25` | `customer123` | Phục vụ kiểm thử tranh mua đồng thời |
| **Driver** | `driver1@foodhub.com` | `driver123` | Bật/tắt online, nhận đơn giao, cập nhật toạ độ GPS |
| **Driver** | `driver2@foodhub.com` | `driver123` | Tài xế 2 (Biển số: `59-A1 99999`) |

**Mã Voucher mẫu:**
* `FOODHUB50`: Giảm 50% (tối đa 40.000đ cho đơn từ 50.000đ).
* `FREESHIP20K`: Giảm 20.000đ phí giao hàng cho đơn từ 60.000đ.
* `FLASH5`: Giảm 30.000đ (giới hạn đúng 5 lượt toàn hệ thống để test tranh voucher).

---

## 🧭 Danh mục API Chính (API Surface)

### 1. Xác thực (Authentication)
* `POST /api/v1/auth/register` — Đăng ký tài khoản mới.
* `POST /api/v1/auth/login` — Đăng nhập nhận Access + Refresh token (có rate limit 5 req/phút).
* `POST /api/v1/auth/refresh` — Làm mới Access token.
* `GET /api/v1/auth/me` — Lấy thông tin cá nhân của người dùng hiện tại.

### 2. Quán ăn & Thực đơn (Merchant & Menu)
* `GET /api/v1/restaurants/` — Danh sách quán ăn (hỗ trợ tìm kiếm theo tên).
* `POST /api/v1/restaurants/` — Tạo quán ăn mới (Merchant / Admin).
* `GET /api/v1/menu/restaurants/{id}/items` — Xem menu quán ăn (tự động **Cache-Aside** trong Redis 3600s).
* `POST /api/v1/menu/restaurants/{id}/items` — Thêm món mới (**tự động xóa cache menu**).
* `PATCH /api/v1/menu/items/{id}` — Cập nhật thông tin món / số lượng tồn kho (**tự động xóa cache**).

### 3. Đơn hàng (Orders & State Machine)
* `POST /api/v1/orders/` — Tạo đơn hàng mới (**Transaction ACID, snapshot giá, trừ kho atomic, hỗ trợ `X-Idempotency-Key`, rate limit 5 đơn/phút, kích hoạt Celery auto-cancel 15p & WebSocket**).
* `GET /api/v1/orders/` — Danh sách đơn của tôi (tối ưu Eager Loading, **không bị N+1 query**).
* `GET /api/v1/orders/{id}` — Chi tiết đơn hàng + danh sách món + lịch sử chuyển trạng thái FSM.
* `PATCH /api/v1/orders/{id}/status` — Chuyển trạng thái đơn hàng theo đúng máy trạng thái (FSM) và đúng vai trò (Customer/Merchant/Driver/Admin). Tự động hoàn kho khi đơn bị hủy (`CANCELLED`).

### 4. Tài xế & Vận chuyển (Driver)
* `GET /api/v1/driver/profile` — Xem hồ sơ tài xế hiện tại.
* `PATCH /api/v1/driver/status` — Bật/tắt trạng thái sẵn sàng (Online/Offline).
* `POST /api/v1/driver/deliveries/{order_id}/accept` — Tài xế nhận đơn hàng đang chờ giao (`READY_FOR_PICKUP`). Có cơ chế khóa chống tranh chấp (`409 Conflict`).
* `POST /api/v1/driver/location` — Cập nhật vị trí GPS (lưu Redis TTL 300s, cập nhật DB và phát realtime WebSocket tới khách).

### 5. Báo cáo & Thống kê (Reports)
* `GET /api/v1/reports/revenue` — Báo cáo doanh thu theo ngày, top món bán chạy (SQL Aggregation + **Redis Cache TTL 600s** + **Tự động xóa cache khi có đơn DELIVERED**).

### 6. Quản trị viên (Admin & Audit Trail)
* `GET /api/v1/admin/orders` — Xem danh sách toàn bộ đơn hàng trên hệ thống.
* `POST /api/v1/admin/orders/{id}/refund` — Hoàn tiền đơn bị hủy/thất bại (**ghi nhận Audit Log bất biến**).
* `GET /api/v1/admin/audit-logs` — Xem nhật ký kiểm toán hệ thống.

### 7. Thời gian thực (WebSocket)
* `WS /ws/orders/{id}?token=<jwt>` — Kênh theo dõi tiến độ đơn hàng và vị trí tài xế (Khách / Quán / Admin).
* `WS /ws/merchant/feed?token=<jwt>` — Kênh nhận đơn mới realtime dành cho Quán ăn.

---

## 📂 Cấu trúc Thư mục Dự án

```text
├── app/
│   ├── api/                    # Tầng API endpoints & dependencies
│   │   ├── deps.py             # Auth dependencies & RBAC guards
│   │   └── v1/
│   │       ├── router.py       # API router tổng hợp
│   │       └── endpoints/      # Controllers (auth, orders, menu, drivers, reports, admin, ws)
│   ├── core/                   # Cấu hình lõi (database, redis, celery, security, rate limiter)
│   ├── middleware/             # Middleware (OWASP Security Headers)
│   ├── models/                 # SQLAlchemy 2.0 ORM Models (User, Order, Restaurant, MenuItem, ...)
│   ├── schemas/                # Pydantic v2 DTOs (Request / Response validation)
│   ├── services/               # Nghiệp vụ lõi (order_service, state_machine, report_service, ws_manager)
│   ├── tasks/                  # Celery background & scheduled tasks (order, payment, notification, reconciliation)
│   └── main.py                 # FastAPI application factory & lifespan
├── docs/
│   └── architecture.md         # Tài liệu kiến trúc sơ bộ
├── requirements/
│   └── foodhub-backend-training-spec.html  # Đề bài đặc tả nghiệp vụ gốc từ Mentor
├── scripts/
│   ├── seed.py                 # Script tạo dữ liệu mẫu cho hệ thống
│   └── load_test.py            # Script kiểm thử tải đồng thời (Zero-Oversell test)
├── tests/                      # Bộ kiểm thử tự động (39 bài test bao phủ trọn vẹn 5 trụ cột)
├── ARCHITECTURE.md             # Bản mô tả kiến trúc chuyên sâu giải trình 5 trụ cột
├── docker-compose.yml          # Cấu hình Docker Compose (api, postgres, redis, worker, beat)
├── Dockerfile                  # Cấu hình đóng gói container FastAPI
├── pyproject.toml              # Cấu hình Pytest & Ruff linter
└── README.md                   # Tài liệu hướng dẫn sử dụng & bàn giao dự án
```

---

## 📖 Bản mô tả Kiến trúc Chi tiết
Vui lòng tham khảo file [`ARCHITECTURE.md`](file:///d:/Backend%20Developer/Food%20Ordering%20&%20Delivery%20System/ARCHITECTURE.md) để xem chi tiết sơ đồ luồng (Sequence Diagrams), sơ đồ thực thể (ERD), máy trạng thái (FSM) và phân tích sâu về các giải pháp kỹ thuật của 5 trụ cột.

