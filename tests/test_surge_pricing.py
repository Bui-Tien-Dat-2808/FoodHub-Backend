from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User
from app.services.pricing_service import (
    VN_TZ,
    calculate_delivery_fee,
    calculate_voucher_discount,
    get_surge_multiplier,
)


def test_get_surge_multiplier_peak_hours():
    """Kiểm tra tính toán Surge Multiplier theo từng khung giờ trong ngày (giờ VN)."""
    # 12:00 trưa VN -> Giờ cao điểm trưa (x1.25)
    lunch_time = datetime(2026, 9, 22, 12, 0, 0, tzinfo=VN_TZ)
    m_lunch, reason_lunch = get_surge_multiplier(target_time=lunch_time)
    assert m_lunch == 1.25
    assert "trưa" in reason_lunch.lower()

    # 19:00 tối VN -> Giờ cao điểm tối (x1.35)
    dinner_time = datetime(2026, 9, 22, 19, 0, 0, tzinfo=VN_TZ)
    m_dinner, reason_dinner = get_surge_multiplier(target_time=dinner_time)
    assert m_dinner == 1.35
    assert "tối" in reason_dinner.lower()

    # 15:00 chiều VN -> Giờ bình thường (x1.0)
    normal_time = datetime(2026, 9, 22, 15, 0, 0, tzinfo=VN_TZ)
    m_normal, reason_normal = get_surge_multiplier(target_time=normal_time)
    assert m_normal == 1.0
    assert "tiêu chuẩn" in reason_normal.lower()


def test_get_surge_multiplier_with_high_demand():
    """Kiểm tra khi nhu cầu đặt hàng cục bộ tăng cao (demand_ratio >= 2.0)."""
    # 15:00 bình thường nhưng tỷ lệ đơn/tài xế = 2.5 -> 1.0 + 0.2 = 1.2
    normal_time = datetime(2026, 9, 22, 15, 0, 0, tzinfo=VN_TZ)
    m_demand, reason_demand = get_surge_multiplier(target_time=normal_time, demand_ratio=2.5)
    assert m_demand == 1.2
    assert "nhu cầu" in reason_demand.lower()

    # Giới hạn trần tối đa không vượt quá 2.0x
    dinner_time = datetime(2026, 9, 22, 19, 0, 0, tzinfo=VN_TZ)
    m_cap, _ = get_surge_multiplier(target_time=dinner_time, demand_ratio=10.0)
    assert m_cap <= 2.0


def test_calculate_delivery_fee_with_surge():
    """Kiểm tra tính phí giao hàng kết hợp với hệ số Surge Multiplier."""
    # 1.5 km (trong 2km đầu: cước gốc 15.000đ)
    assert calculate_delivery_fee(1.5, surge_multiplier=1.0) == 15000
    assert calculate_delivery_fee(1.5, surge_multiplier=1.2) == 18000
    assert calculate_delivery_fee(1.5, surge_multiplier=1.35) == 20250

    # 3.2 km (vượt 1.2km -> ceil = 2km phụ trội: cước gốc 15000 + 2*5000 = 25.000đ)
    assert calculate_delivery_fee(3.2, surge_multiplier=1.0) == 25000
    assert calculate_delivery_fee(3.2, surge_multiplier=1.25) == 31250


def test_calculate_voucher_discount():
    """Kiểm tra tính giảm giá voucher."""
    # PERCENTAGE có max discount
    assert calculate_voucher_discount(subtotal=100000, discount_type="PERCENTAGE", discount_value=20, max_discount=15000) == 15000
    # PERCENTAGE không vượt quá subtotal
    assert calculate_voucher_discount(subtotal=100000, discount_type="PERCENTAGE", discount_value=10, max_discount=0) == 10000
    # FIXED discount
    assert calculate_voucher_discount(subtotal=100000, discount_type="FIXED", discount_value=30000) == 30000
    assert calculate_voucher_discount(subtotal=20000, discount_type="FIXED", discount_value=50000) == 20000


@pytest.mark.anyio
async def test_estimate_delivery_fee_endpoint(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra endpoint GET /api/v1/pricing/estimate-delivery."""
    # 1. Tạo nhà hàng
    owner = User(
        email="owner_pricing@foodhub.com",
        hashed_password="hash",
        full_name="Pricing Owner",
        role="MERCHANT",
    )
    db_session.add(owner)
    await db_session.commit()

    restaurant = Restaurant(
        owner_id=owner.id,
        name="Quan Com Van Phong",
        address="10 Le Duan, Q1",
        latitude=10.7800,
        longitude=106.7000,
        is_open=True,
    )
    db_session.add(restaurant)
    await db_session.commit()

    # 2. Gọi qua restaurant_id
    res1 = await client.get(
        f"/api/v1/pricing/estimate-delivery?restaurant_id={restaurant.id}&delivery_lat=10.7900&delivery_lng=106.7100"
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["distance_km"] > 0
    assert data1["base_delivery_fee"] >= 15000
    assert data1["surge_multiplier"] >= 1.0
    assert data1["final_delivery_fee"] >= data1["base_delivery_fee"]
    assert "surge_reason" in data1

    # 3. Gọi qua toạ độ origin_lat/origin_lng
    res2 = await client.get(
        "/api/v1/pricing/estimate-delivery?origin_lat=10.7800&origin_lng=106.7000&delivery_lat=10.7900&delivery_lng=106.7100"
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["distance_km"] == data1["distance_km"]

    # 4. Gọi thiếu cả restaurant_id và origin -> 400 Bad Request
    res3 = await client.get("/api/v1/pricing/estimate-delivery?delivery_lat=10.7900&delivery_lng=106.7100")
    assert res3.status_code == 400


@pytest.mark.anyio
async def test_order_creation_includes_surge_multiplier(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra tạo đơn hàng thực tế lưu trữ đúng surge_multiplier và tính đúng cước phí."""
    # 1. Đăng ký & Đăng nhập Customer
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "surge_cust@foodhub.com",
            "password": "password123",
            "full_name": "Surge Customer",
            "role": "CUSTOMER",
        },
    )
    token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "surge_cust@foodhub.com", "password": "password123"},
        )
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Tạo Nhà hàng & Món ăn
    owner = User(
        email="surge_merchant@foodhub.com",
        hashed_password="hash",
        full_name="Surge Merchant",
        role="MERCHANT",
    )
    db_session.add(owner)
    await db_session.commit()

    rest = Restaurant(
        owner_id=owner.id,
        name="Com Tam Dem",
        address="99 Tran Hung Dao",
        latitude=10.7600,
        longitude=106.6900,
        is_open=True,
    )
    db_session.add(rest)
    await db_session.commit()

    item = MenuItem(
        restaurant_id=rest.id,
        name="Suon Nuong",
        base_price=45000,
        stock_quantity=30,
        is_available=True,
    )
    db_session.add(item)
    await db_session.commit()

    # 3. Đặt hàng
    order_res = await client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "restaurant_id": rest.id,
            "delivery_address": "88 Nguyen Thi Minh Khai",
            "delivery_lat": 10.7700,
            "delivery_lng": 106.7000,
            "items": [{"menu_item_id": item.id, "quantity": 2}],
        },
    )
    assert order_res.status_code == 201
    order_data = order_res.json()

    assert "surge_multiplier" in order_data
    assert order_data["surge_multiplier"] >= 1.0
    assert order_data["delivery_fee"] >= 15000
    assert order_data["subtotal"] == 90000
    assert order_data["total_amount"] == 90000 + order_data["delivery_fee"]

