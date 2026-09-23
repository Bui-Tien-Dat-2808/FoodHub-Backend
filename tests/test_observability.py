
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User


@pytest.mark.anyio
async def test_health_live_probe(client: AsyncClient):
    """Kiểm tra Liveness Probe: process Uvicorn hoạt động bình thường."""
    res = await client.get("/health/live")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "alive"
    assert "version" in data
    assert "service" in data

    # Root /health backward-compatibility
    res_root = await client.get("/health")
    assert res_root.status_code == 200
    assert res_root.json()["status"] == "healthy"


@pytest.mark.anyio
async def test_health_ready_probe_happy_path(client: AsyncClient):
    """Kiểm tra Readiness Probe khi cả PostgreSQL và Redis đều kết nối tốt."""
    res = await client.get("/health/ready")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["dependencies"]["database"] == "connected"
    assert data["dependencies"]["redis"] == "connected"


@pytest.mark.anyio
async def test_health_ready_probe_db_failure(client: AsyncClient, monkeypatch):
    """Kiểm tra Readiness Probe trả về 503 khi cơ sở dữ liệu gặp sự cố."""
    # Mock hàm execute của DB session báo lỗi
    async def mock_execute(*args, **kwargs):
        raise ConnectionRefusedError("Database connection lost")

    monkeypatch.setattr(AsyncSession, "execute", mock_execute)

    res = await client.get("/health/ready")
    assert res.status_code == 503
    data = res.json()
    assert data["status"] == "not_ready"
    assert "unhealthy" in data["dependencies"]["database"]


@pytest.mark.anyio
async def test_prometheus_metrics_endpoint_and_middleware(client: AsyncClient):
    """
    Kiểm tra endpoint GET /metrics và Prometheus Middleware:
    1. Gọi một số API để sinh metrics
    2. Kiểm tra /metrics trả về text định dạng chuẩn Prometheus
    3. Kiểm tra các metric chính: http_requests_total, duration_seconds, orders_created, websocket
    """
    # 1. Gọi một request để middleware ghi nhận
    await client.get("/health/live")

    # 2. Lấy metrics
    res = await client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers.get("content-type", "")

    body = res.text
    # Kiểm tra sự xuất hiện của các metrics cốt lõi
    assert "foodhub_http_requests_total" in body
    assert "foodhub_http_request_duration_seconds" in body
    assert "foodhub_active_websocket_connections" in body
    assert "foodhub_orders_created_total" in body


@pytest.mark.anyio
async def test_order_creation_updates_prometheus_counter(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra khi đơn hàng được tạo thì metric foodhub_orders_created_total được tăng lên."""
    # 1. Đăng ký & Đăng nhập Customer
    await client.post("/api/v1/auth/register", json={
        "email": "metric_buyer@foodhub.com",
        "password": "password123",
        "full_name": "Metric Buyer",
        "role": "CUSTOMER",
    })
    token = (await client.post("/api/v1/auth/login", json={
        "email": "metric_buyer@foodhub.com",
        "password": "password123",
    })).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Tạo Nhà hàng & Món ăn
    owner = User(
        email="metric_owner@foodhub.com",
        hashed_password="hash",
        full_name="Metric Owner",
        role="MERCHANT",
    )
    db_session.add(owner)
    await db_session.commit()

    rest = Restaurant(
        owner_id=owner.id,
        name="Quan Metric Test",
        address="10 Nguyen Thi Dinh",
        latitude=10.8000,
        longitude=106.7200,
        is_open=True,
    )
    db_session.add(rest)
    await db_session.commit()

    item = MenuItem(
        restaurant_id=rest.id,
        name="Tra Sua Metric",
        base_price=35000,
        stock_quantity=50,
        is_available=True,
    )
    db_session.add(item)
    await db_session.commit()

    # 3. Tạo đơn hàng
    order_res = await client.post(
        "/api/v1/orders/",
        headers=headers,
        json={
            "restaurant_id": rest.id,
            "items": [{"menu_item_id": item.id, "quantity": 1}],
            "delivery_address": "Khu Cong Nghe Cao",
            "delivery_lat": 10.8500,
            "delivery_lng": 106.7700,
        },
    )
    assert order_res.status_code == 201

    # 4. Kiểm tra /metrics có ghi nhận đơn hàng mới
    metrics_res = await client.get("/metrics")
    assert metrics_res.status_code == 200
    assert "foodhub_orders_created_total" in metrics_res.text

