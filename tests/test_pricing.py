import math


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Tính khoảng cách giữa 2 tọa độ theo km dùng công thức Haversine."""
    R = 6371.0  # Bán kính Trái Đất (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)


def calculate_delivery_fee(distance_km: float, base_fee: int = 15000, base_km: float = 2.0, rate_per_km: int = 5000) -> int:
    """
    Quy tắc tính phí giao hàng:
    - Dưới hoặc bằng base_km (2km đầu): base_fee (15.000đ).
    - Vượt quá base_km: mỗi km tiếp theo cộng rate_per_km (5.000đ/km), làm tròn lên km tiếp theo.
    """
    if distance_km <= base_km:
        return base_fee
    extra_km = math.ceil(distance_km - base_km)
    return base_fee + int(extra_km * rate_per_km)


def calculate_voucher_discount(subtotal: int, discount_type: str, discount_value: int, max_discount: int = 0) -> int:
    """
    Tính tiền giảm giá từ voucher:
    - PERCENTAGE: Giảm theo %, có chặn trần max_discount nếu max_discount > 0.
    - FIXED: Giảm cố định, không vượt quá subtotal.
    """
    if discount_type == "PERCENTAGE":
        raw_discount = int(subtotal * (discount_value / 100))
        if max_discount > 0:
            return min(raw_discount, max_discount)
        return min(raw_discount, subtotal)
    elif discount_type == "FIXED":
        return min(discount_value, subtotal)
    return 0


# --- TEST CASES ĐỂ BẠN CHẠY KIỂM CHỨNG CODE TAY ---

def test_haversine_same_point():
    assert haversine_distance(10.7769, 106.7009, 10.7769, 106.7009) == 0.0

def test_haversine_known_distance():
    # Khoảng cách từ Chợ Bến Thành (10.7725, 106.6980) đến Landmark 81 (10.7950, 106.7219) ~ 3.5 - 3.7 km
    d = haversine_distance(10.7725, 106.6980, 10.7950, 106.7219)
    assert 3.0 <= d <= 4.0

def test_delivery_fee_within_base():
    assert calculate_delivery_fee(1.5) == 15000
    assert calculate_delivery_fee(2.0) == 15000

def test_delivery_fee_extra_distance():
    # 3.2 km -> dư 1.2km -> ceil thành 2km phụ trội -> 15000 + 2*5000 = 25000
    assert calculate_delivery_fee(3.2) == 25000

def test_percentage_voucher_with_cap():
    # Đơn 200.000đ, giảm 50%, tối đa 50.000đ -> Giảm 50.000đ
    discount = calculate_voucher_discount(subtotal=200000, discount_type="PERCENTAGE", discount_value=50, max_discount=50000)
    assert discount == 50000

def test_fixed_voucher():
    # Đơn 40.000đ, voucher giảm 50.000đ -> Chỉ giảm tối đa bằng subtotal (40.000đ)
    discount = calculate_voucher_discount(subtotal=40000, discount_type="FIXED", discount_value=50000)
    assert discount == 40000
