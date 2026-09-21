# BẢN MÔ TẢ KIẾN TRÚC HỆ THỐNG FOODHUB
*(Food Ordering & Delivery Platform Architecture Document)*

---

## 1. TỔNG QUAN HỆ THỐNG & C4 CONTEXT

**FoodHub** là hệ thống Backend cung cấp nền tảng kết nối 4 nhóm người dùng chính:
- **Khách hàng (Customer)**: Xem nhà hàng, menu, giỏ hàng, áp mã khuyến mãi, đặt đơn và theo dõi trạng thái đơn hàng theo thời gian thực.
- **Chủ quán (Merchant)**: Quản lý danh mục món ăn (menu), cập nhật số lượng tồn trong ngày (daily inventory), tiếp nhận và cập nhật tiến độ chế biến món ăn.
- **Tài xế (Driver)**: Bật trạng thái sẵn sàng (online/idle), quét tìm các đơn hàng đang chờ giao quanh tọa độ hiện tại, tranh nhận đơn (claim order) và cập nhật tiến trình giao.
- **Quản trị viên (Admin)**: Quản lý người dùng, tạo chiến dịch khuyến mãi (voucher campaigns) với quota và điều kiện giới hạn.

`mermaid
graph TD
    Customer[Customer App / Web] -->|HTTP / REST| API[FoodHub API Gateway / FastAPI]
    Merchant[Merchant Portal] -->|HTTP / REST| API
    Driver[Driver App] -->|HTTP / REST| API
    Admin[Admin Dashboard] -->|HTTP / REST| API
    
    API -->|Async ORM / Transactions| DB[(PostgreSQL 16)]
    API -->|Caching & Distributed Lock| Redis[(Redis 7)]
`

---

## 2. THIẾT KẾ CSDL & SƠ ĐỒ THỰC THỂ (ERD)

`mermaid
erDiagram
    USERS ||--o{ RESTAURANTS : owns
    USERS ||--o{ ORDERS : places
    USERS ||--o| DRIVER_PROFILES : has
    USERS ||--o{ VOUCHER_USAGES : uses
    
    RESTAURANTS ||--o{ MENU_ITEMS : contains
    RESTAURANTS ||--o{ ORDERS : receives
    
    MENU_ITEMS ||--o{ OPTION_GROUPS : has
    OPTION_GROUPS ||--o{ OPTION_ITEMS : has
    
    ORDERS ||--o{ ORDER_ITEMS : includes
    ORDERS ||--o{ ORDER_STATUS_HISTORIES : tracks
    ORDERS ||--o| VOUCHERS : applies
    ORDERS ||--o| DELIVERY_ASSIGNMENTS : assigned_to
    
    DRIVER_PROFILES ||--o{ DELIVERY_ASSIGNMENTS : delivers
`

### Chi tiết các bảng nghiệp vụ chính:
1. users: id, email, hashed_password, ull_name, phone, ole (CUSTOMER, MERCHANT, DRIVER, ADMIN), is_active, created_at.
2. estaurants: id, owner_id, 
ame, ddress, latitude, longitude, is_open, created_at.
3. menu_items: id, estaurant_id, 
ame, description, ase_price, stock_quantity (kiểm soát overselling), is_available.
4. ouchers: id, code, discount_type (PERCENTAGE / FIXED), discount_value, min_order_value, max_discount, usage_limit (toàn hệ thống), used_count, per_user_limit, alid_from, alid_to.
5. oucher_usages: id, oucher_id, user_id, order_id, used_at (Chống 1 user dùng vượt quota).
6. orders: id, order_code, customer_id, estaurant_id, status, delivery_lat, delivery_lng, subtotal, delivery_fee, discount_amount, 	otal_amount, oucher_id, idempotency_key, created_at.
7. order_status_histories: id, order_id, rom_status, 	o_status, changed_by_user_id, eason, created_at.
8. driver_assignments: id, order_id, driver_id, status (ACCEPTED, ARRIVED_AT_STORE, DELIVERING, COMPLETED, CANCELLED), claimed_at.

---

## 3. VÒNG ĐỜI ĐƠN HÀNG (ORDER STATE MACHINE)

Vòng đời đơn hàng là Finite State Machine (FSM) nghiêm ngặt để loại trừ việc chuyển trạng thái bất hợp lệ.

`mermaid
stateDiagram-v2
    [*] --> SUBMITTED : Khách đặt đơn (giữ tồn kho)
    SUBMITTED --> MERCHANT_ACCEPTED : Nhà hàng duyệt đơn
    SUBMITTED --> CANCELLED : Khách/Quán hủy (hoàn tồn kho + voucher)
    
    MERCHANT_ACCEPTED --> PREPARING : Bắt đầu nấu
    MERCHANT_ACCEPTED --> CANCELLED : Quán hết nguyên liệu (hoàn tồn kho)
    
    PREPARING --> READY_FOR_PICKUP : Nấu xong, bắn lên sàn tìm tài xế
    
    READY_FOR_PICKUP --> DRIVER_ASSIGNED : Tài xế nhận đơn thành công
    
    DRIVER_ASSIGNED --> PICKED_UP : Tài xế lấy hàng tại quán
    DRIVER_ASSIGNED --> READY_FOR_PICKUP : Tài xế sự cố, nhả đơn về sàn
    
    PICKED_UP --> DELIVERED : Giao hàng thành công
    PICKED_UP --> FAILED_DELIVERY : Không liên lạc được khách
    
    DELIVERED --> [*]
    CANCELLED --> [*]
    FAILED_DELIVERY --> [*]
`

### Bảng chuyển đổi hợp lệ (Transition Matrix):
| Trạng thái hiện tại (rom_status) | Trạng thái tiếp theo hợp lệ (	o_status) | Ai được phép kích hoạt |
| :--- | :--- | :--- |
| SUBMITTED | MERCHANT_ACCEPTED, CANCELLED | Quán (nhận), Khách / Quán (hủy) |
| MERCHANT_ACCEPTED | PREPARING, CANCELLED | Quán |
| PREPARING | READY_FOR_PICKUP | Quán |
| READY_FOR_PICKUP | DRIVER_ASSIGNED, CANCELLED | Tài xế (nhận đơn), Admin (hủy) |
| DRIVER_ASSIGNED | PICKED_UP, READY_FOR_PICKUP (nhả đơn) | Tài xế |
| PICKED_UP | DELIVERED, FAILED_DELIVERY | Tài xế |

---

## 4. PRICING & PROMOTION ENGINE (TÍNH GIÁ & KHUYẾN MÃI)

### 4.1. Công thức tính giá
\text{Total} = \text{Subtotal (Món + Topping)} + \text{DeliveryFee} - \text{Discount}

Trong đó:
- **Khoảng cách (Haversine Formula)**:
  d = 2R \times \arcsin\left(\sqrt{\sin^2\left(\frac{\Delta \text{lat}}{2}\right) + \cos(\text{lat}_1)\cos(\text{lat}_2)\sin^2\left(\frac{\Delta \text{lng}}{2}\right)}\right)
- **Phí giao hàng (DeliveryFee)**:
  \text{DeliveryFee} = \text{BaseFee} + \max(0, d - d_{\text{base}}) \times \text{RatePerKm}
  *(Ví dụ: 2km đầu là 15.000đ, mỗi km tiếp theo cộng 5.000đ)*.
- **Giảm giá Voucher**:
  - Nếu discount_type == 'PERCENTAGE': $\min(\text{Subtotal} \times \text{value}\%, \text{max\_discount})$
  - Nếu discount_type == 'FIXED': $\min(\text{value}, \text{Subtotal})$

### 4.2. Bài toán Tải đồng thời: Tranh Voucher (Race Condition)
- **Vấn đề**: Voucher giảm 50% chỉ có 100 lượt dùng. 500 khách đồng thời nhấn thanh toán tại 1 giây.
- **Hậu quả nếu code ngây thơ (Read-then-Write)**:
  `python
  # NGUY HIỂM: Lost update! Cả 500 request đọc used_count = 99 -> đều hợp lệ -> used_count vọt lên 500!
  if voucher.used_count < voucher.usage_limit:
      voucher.used_count += 1
  `
- **Giải pháp Production**: **Atomic Update with Condition**
  `sql
  UPDATE vouchers 
  SET used_count = used_count + 1 
  WHERE id = :voucher_id 
    AND is_active = TRUE 
    AND used_count < usage_limit 
  RETURNING id, used_count;
  `
  Nếu kết quả trả về None (0 rows affected) $\rightarrow$ Ngay lập tức từ chối voucher với lỗi: *"Mã khuyến mãi đã hết lượt sử dụng"*. Đồng thời ghi nhận vào bảng oucher_usages với Unique Constraint (voucher_id, user_id) để ngăn 1 user gian lận dùng nhiều lần.

---

## 5. DRIVER MATCHING UNDER CONCURRENCY (TRANH NHẬN ĐƠN)

### 5.1. Mô hình Broadcast Pool
Khi đơn ở trạng thái READY_FOR_PICKUP:
1. API phát đơn vào danh sách chờ của các tài xế có khoảng cách $\le 3\text{km}$ tới quán ăn.
2. Nhiều tài xế cùng nhìn thấy đơn và cùng nhấn nút **"Nhận đơn" (Claim Order)**.

### 5.2. Kỹ thuật chống Race Condition: SELECT FOR UPDATE SKIP LOCKED
- **Mục tiêu**: Đảm bảo đúng 1 tài xế nhận đơn thành công, các tài xế khác nhận phản hồi tức thì *"Đơn đã được tài xế khác nhận"* mà **KHÔNG** bị deadlock hoặc đợi timeout.
- **Giải pháp SQL chuẩn High-Concurrency**:
  `sql
  -- Bắt đầu Transaction
  SELECT id, status 
  FROM orders 
  WHERE id = :order_id 
    AND status = 'READY_FOR_PICKUP'
  FOR UPDATE SKIP LOCKED;
  `
  - Nếu row bị lock bởi request của Tài xế A: Request của Tài xế B khi gọi SKIP LOCKED sẽ không đọc được row đó (trả về Empty) $\rightarrow$ Trả về mã lỗi 409 Conflict: Đơn hàng đã có người khác nhận.
  - Không có tình trạng 2 tài xế cùng nhận 1 đơn, cũng không bị nghẽn thread pool.

---

## 6. KIỂM SOÁT TỒN KHO MÓN ĂN (ANTI-OVERSELLING)

- **Bài toán**: Món đặc biệt (Special Dish) có số lượng stock_quantity = 5. 20 khách hàng cùng bấm đặt món tại cùng một thời điểm.
- **Giải pháp**: **Atomic Decrement in DB Transaction**
  `sql
  UPDATE menu_items 
  SET stock_quantity = stock_quantity - :qty 
  WHERE id = :item_id 
    AND stock_quantity >= :qty 
  RETURNING id, stock_quantity;
  `
  - Chỉ cần 1 câu lệnh nguyên tử, Database engine sẽ serialize việc trừ kho. Nếu tồn kho không đủ cho :qty, câu query trả về 0 row $\rightarrow$ Bắn lỗi 400 Bad Request: Món đã hết số lượng phục vụ trong ngày.
  - Nếu quá trình thanh toán thất bại hoặc đơn bị hủy trước khi quán làm, hệ thống tự động hoàn kho:
    UPDATE menu_items SET stock_quantity = stock_quantity + :qty WHERE id = :item_id.

---

## 7. IDEMPOTENCY KEY (CHỐNG DOUBLE-CHARGE / ĐẶT ĐƠN TRÙNG)
- Khách hàng bấm nút "Thanh toán" nhưng mạng lag, người dùng bấm tiếp lần 2.
- Client sinh mã UUID Idempotency-Key gửi kèm header HTTP X-Idempotency-Key.
- Backend lưu key vào Redis với TTL 120s hoặc lưu vào cột orders.idempotency_key (UNIQUE). Nếu phát hiện key đã tồn tại $\rightarrow$ Trả về kết quả đơn hàng đã tạo trước đó thay vì tạo thêm đơn mới và trừ tiền 2 lần.
