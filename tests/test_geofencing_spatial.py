"""Tests for Geospatial Data Upgrade: Spatial Indexing & Geofencing.

Validates:
- Bounding Box calculations and mathematical bounds
- Ray-Casting Point-in-Polygon geofencing algorithm
- Restaurant delivery radius geofencing enforcement on order creation (HTTP 400 when out of zone)
- Merchant/Admin delivery zone configuration endpoint (PATCH /delivery-zone) and RBAC
- Spatial bounding-box query optimization for driver matching
- Delivery fee estimation geofencing flags
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import DriverProfile
from app.models.restaurant import Restaurant
from app.services.driver_matching_service import find_nearby_available_drivers
from app.services.geo_service import (
    get_bounding_box,
    is_point_in_polygon,
    is_point_within_radius,
)


def test_bounding_box_calculation():
    """Kiểm tra thuật toán tính toán Bounding Box bao quanh toạ độ."""
    center_lat = 10.7769
    center_lon = 106.7009
    radius_km = 5.0

    min_lat, max_lat, min_lon, max_lon = get_bounding_box(center_lat, center_lon, radius_km)

    assert min_lat < center_lat < max_lat
    assert min_lon < center_lon < max_lon

    # Bán kính 0 trả về chính toạ độ tâm
    assert get_bounding_box(center_lat, center_lon, 0.0) == (center_lat, center_lat, center_lon, center_lon)

    # Điểm cách 1km phải nằm trong Bounding Box 5km
    assert min_lat <= 10.7800 <= max_lat
    assert min_lon <= 106.7050 <= max_lon


def test_ray_casting_point_in_polygon():
    """Kiểm tra thuật toán Ray-Casting xác định điểm trong/ngoài ranh giới Polygon."""
    # Vùng đa giác mô phỏng một quận (hình vuông 10.0 - 10.5 lat, 106.0 - 106.5 lon)
    district_polygon = [
        (10.0, 106.0),
        (10.0, 106.5),
        (10.5, 106.5),
        (10.5, 106.0),
    ]

    # Điểm ở giữa -> True
    assert is_point_in_polygon(10.25, 106.25, district_polygon) is True

    # Điểm ngoài vùng -> False
    assert is_point_in_polygon(10.80, 106.25, district_polygon) is False
    assert is_point_in_polygon(10.25, 106.90, district_polygon) is False

    # Đa giác không hợp lệ (< 3 đỉnh) -> False
    assert is_point_in_polygon(10.25, 106.25, [(10.0, 106.0), (10.0, 106.5)]) is False


def test_is_point_within_radius():
    """Kiểm tra hàm helper xác định bán kính ranh giới."""
    center_lat, center_lon = 10.7769, 106.7009
    # Điểm rất gần (khoảng 350m)
    assert is_point_within_radius(center_lat, center_lon, 10.7790, 106.7020, radius_km=1.0) is True
    # Điểm xa 15km
    assert is_point_within_radius(center_lat, center_lon, 10.9000, 106.7009, radius_km=5.0) is False


@pytest.mark.asyncio
async def test_order_creation_geofencing_rejection_and_success(client: AsyncClient):
    """Kiểm tra đơn hàng bị từ chối khi giao ngoài bán kính phục vụ của quán."""
    # 1. Đăng ký Merchant & tạo nhà hàng có bán kính giao hàng 5.0 km
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_geo@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Geofence",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_geo@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Com Tam Geofence",
        "address": "123 Nguyen Thi Minh Khai, Q1",
        "latitude": 10.7769,
        "longitude": 106.7009,
        "delivery_radius_km": 5.0,
    })
    assert rest_res.status_code == 201
    rest_data = rest_res.json()
    rest_id = rest_data["id"]
    assert rest_data["delivery_radius_km"] == 5.0

    # Thêm món ăn
    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Com Suon Bi Cha",
        "base_price": 50000,
        "stock_quantity": 50,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_geo@foodhub.com",
        "password": "password123",
        "full_name": "Customer Geofence",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_geo@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    # 3. Customer đặt đơn ở toạ độ cách quán ~15km (ngoài bán kính 5km) -> Bị từ chối HTTP 400
    far_order_res = await client.post("/api/v1/orders/", headers=c_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 1}],
        "delivery_lat": 10.9100,  # Rất xa
        "delivery_lng": 106.7009,
        "delivery_address": "Dia chi rat xa"
    })
    assert far_order_res.status_code == 400
    assert "vượt quá bán kính phục vụ" in far_order_res.json()["detail"]

    # 4. Customer đặt đơn ở toạ độ cách quán ~1km (trong bán kính 5km) -> Thành công HTTP 201
    near_order_res = await client.post("/api/v1/orders/", headers=c_headers, json={
        "restaurant_id": rest_id,
        "items": [{"menu_item_id": item_id, "quantity": 1}],
        "delivery_lat": 10.7850,
        "delivery_lng": 106.7050,
        "delivery_address": "Dia chi gan quan"
    })
    assert near_order_res.status_code == 201


@pytest.mark.asyncio
async def test_update_restaurant_delivery_zone_rbac(client: AsyncClient):
    """Kiểm tra endpoint PATCH /restaurants/{id}/delivery-zone và phân quyền RBAC."""
    # 1. Tạo Merchant 1 & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_zone1@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Zone 1",
        "role": "MERCHANT"
    })
    m1_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_zone1@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m1_headers = {"Authorization": f"Bearer {m1_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m1_headers, json={
        "name": "Banh Mi Zone Test",
        "address": "45 Le Duan",
        "latitude": 10.7800,
        "longitude": 106.7000,
        "delivery_radius_km": 7.0,
    })
    rest_id = rest_res.json()["id"]

    # 2. Tạo Merchant 2 (người khác)
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_zone2@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Zone 2",
        "role": "MERCHANT"
    })
    m2_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_zone2@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m2_headers = {"Authorization": f"Bearer {m2_token}"}

    # Merchant 2 cố cập nhật vùng của quán Merchant 1 -> 403 Forbidden
    forbidden_res = await client.patch(
        f"/api/v1/restaurants/{rest_id}/delivery-zone",
        headers=m2_headers,
        json={"delivery_radius_km": 15.0}
    )
    assert forbidden_res.status_code == 403

    # Merchant 1 cập nhật vùng quán mình -> Thành công 200 OK
    update_res = await client.patch(
        f"/api/v1/restaurants/{rest_id}/delivery-zone",
        headers=m1_headers,
        json={"delivery_radius_km": 15.0}
    )
    assert update_res.status_code == 200
    assert update_res.json()["delivery_radius_km"] == 15.0

    # Validation: bán kính < 0.5 -> 422 Unprocessable Entity
    invalid_res = await client.patch(
        f"/api/v1/restaurants/{rest_id}/delivery-zone",
        headers=m1_headers,
        json={"delivery_radius_km": 0.1}
    )
    assert invalid_res.status_code == 422


@pytest.mark.asyncio
async def test_pricing_estimate_with_geofencing_status(client: AsyncClient):
    """Kiểm tra endpoint estimate-delivery trả về thông tin ranh giới vùng phục vụ."""
    # 1. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_estimate_geo@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Geo Pricing",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_estimate_geo@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Pho Ha Noi Geo Pricing",
        "address": "1 Pasteur",
        "latitude": 10.7700,
        "longitude": 106.7000,
        "delivery_radius_km": 6.0,
    })
    rest_id = rest_res.json()["id"]

    # 2. Ước tính trong vùng (cách 1km)
    near_res = await client.get(
        "/api/v1/pricing/estimate-delivery",
        params={
            "restaurant_id": rest_id,
            "delivery_lat": 10.7780,
            "delivery_lng": 106.7050,
        }
    )
    assert near_res.status_code == 200
    near_data = near_res.json()
    assert near_data["is_within_delivery_zone"] is True
    assert near_data["max_delivery_radius_km"] == 6.0

    # 3. Ước tính ngoài vùng (cách 20km)
    far_res = await client.get(
        "/api/v1/pricing/estimate-delivery",
        params={
            "restaurant_id": rest_id,
            "delivery_lat": 10.9500,
            "delivery_lng": 106.7000,
        }
    )
    assert far_res.status_code == 200
    far_data = far_res.json()
    assert far_data["is_within_delivery_zone"] is False
    assert far_data["max_delivery_radius_km"] == 6.0


@pytest.mark.asyncio
async def test_spatial_bounding_box_driver_query(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra thuật toán tìm tài xế sử dụng Bounding Box lọc chính xác tài xế trong phạm vi."""
    # 1. Tạo tài xế gần (trong bounding box ~1km)
    await client.post("/api/v1/auth/register", json={
        "email": "driver_near@foodhub.com",
        "password": "password123",
        "full_name": "Shipper Near",
        "role": "DRIVER"
    })
    user_near = (await db_session.execute(
        Restaurant.metadata.tables["users"].select().where(Restaurant.metadata.tables["users"].c.email == "driver_near@foodhub.com")
    )).first()

    profile_near = DriverProfile(
        user_id=user_near.id,
        license_plate="59-A1 11111",
        current_lat=10.7750,
        current_lng=106.7010,
        is_online=True,
        is_busy=False,
    )
    db_session.add(profile_near)

    # 2. Tạo tài xế xa (ngoài bounding box ~30km)
    await client.post("/api/v1/auth/register", json={
        "email": "driver_far@foodhub.com",
        "password": "password123",
        "full_name": "Shipper Far",
        "role": "DRIVER"
    })
    user_far = (await db_session.execute(
        Restaurant.metadata.tables["users"].select().where(Restaurant.metadata.tables["users"].c.email == "driver_far@foodhub.com")
    )).first()

    profile_far = DriverProfile(
        user_id=user_far.id,
        license_plate="59-A2 22222",
        current_lat=11.1000,  # Xa hẳn
        current_lng=106.7000,
        is_online=True,
        is_busy=False,
    )
    db_session.add(profile_far)

    await db_session.commit()

    # 3. Tìm tài xế quanh tâm (10.7769, 106.7009) bán kính 5km
    from tests.conftest import FakeRedis
    fake_redis = FakeRedis()

    candidates = await find_nearby_available_drivers(
        db=db_session,
        redis=fake_redis,
        target_lat=10.7769,
        target_lng=106.7009,
        max_radius_km=5.0,
        limit=5,
    )

    # Chỉ tài xế gần được trả về, tài xế xa bị Bounding Box loại bỏ ở Database query
    driver_ids = [c["driver_id"] for c in candidates]
    assert profile_near.id in driver_ids
    assert profile_far.id not in driver_ids
