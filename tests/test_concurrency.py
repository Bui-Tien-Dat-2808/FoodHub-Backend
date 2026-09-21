import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.promotion import Voucher
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_voucher_concurrency_race_condition(client: AsyncClient):
    """
        Test 10 request đồng thời dùng 1 voucher có usage_limit = 2.
    """
    # 1. Tạo Merchant & Nhà hàng & Đồ ăn
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_concur@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Concurrency",
        "role": "MERCHANT"
    })
    m_login = await client.post("/api/v1/auth/login", json={
        "email": "merchant_concur@foodhub.com",
        "password": "password123"
    })
    m_token = m_login.json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Quan Com Concurrency",
        "address": "123 Tran Hung Dao",
        "latitude": 10.7600,
        "longitude": 106.6900
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Com Suon Non",
        "description": "Suon non dac biet",
        "base_price": 50000,
        "stock_quantity": 100,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Admin & Voucher chỉ có 2 lượt dùng
    await client.post("/api/v1/auth/register", json={
        "email": "admin_concur@foodhub.com",
        "password": "password123",
        "full_name": "Admin System",
        "role": "ADMIN"
    })
    await client.post("/api/v1/auth/login", json={
        "email": "admin_concur@foodhub.com",
        "password": "password123"
    })

    # Tạo 10 customer
    customer_tokens = []
    for i in range(10):
        email = f"customer_{i}@foodhub.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "password123",
            "full_name": f"Customer {i}",
            "role": "CUSTOMER"
        })
        login = await client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": f"192.168.1.{i+10}"},
            json={
                "email": email,
                "password": "password123",
            },
        )
        customer_tokens.append(login.json()["access_token"])

    async with TestSessionLocal() as session:
        v = Voucher(
            code="GIAM50K",
            discount_type="FIXED",
            discount_value=20000,
            min_order_value=40000,
            usage_limit=2, # CHỈ CÓ 2 LƯỢT!
            used_count=0,
            is_active=True,
            valid_from=datetime.now(UTC) - timedelta(days=1),
            valid_to=datetime.now(UTC) + timedelta(days=1)
        )
        session.add(v)
        await session.commit()
    # 3. BẮN ĐỒNG THỜI 10 REQUESTS BẰNG ASYNCIO.GATHER
    async def place_order(token: str):
        return await client.post("/api/v1/orders/", headers={"Authorization": f"Bearer {token}"}, json={
            "restaurant_id": rest_id,
            "items": [{"menu_item_id": item_id, "quantity": 1}],
            "voucher_code": "GIAM50K",
            "delivery_lat": 10.7620,
            "delivery_lng": 106.6920,
            "delivery_address": "456 Nguyen Trai"
        })
    tasks = [place_order(token) for token in customer_tokens]
    responses = await asyncio.gather(*tasks)
    # 4. KIỂM CHỨNG (ASSERTION)
    success_responses = [r for r in responses if r.status_code == 201]
    failed_responses = [r for r in responses if r.status_code == 400]
    # Khẳng định đúng 2 người giật được, 8 người bị từ chối
    assert len(success_responses) == 2
    assert len(failed_responses) == 8
    # Kiểm tra DB: used_count phải đúng bằng 2, KHÔNG ĐƯỢC VỌT LÊN 10
    async with TestSessionLocal() as session:
        saved_voucher = (await session.execute(select(Voucher).where(Voucher.code == "GIAM50K"))).scalar_one()
        assert saved_voucher.used_count == 2

