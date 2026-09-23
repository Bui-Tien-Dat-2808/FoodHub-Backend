import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.models.driver import DriverAssignment, DriverProfile
from app.models.enums import DriverAssignmentStatus
from app.models.user import User


@pytest.mark.anyio
async def test_websocket_auth_rejection(client: AsyncClient):
    """Kiểm tra WebSocket từ chối (close code 1008) khi thiếu hoặc sai JWT token."""
    with TestClient(app) as tc:
        # 1. Không có token -> Từ chối 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/ws/orders/1"):
                pass
        assert exc_info.value.code == 1008

        # 2. Token không hợp lệ -> Từ chối 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/ws/orders/1?token=invalid_jwt_token"):
                pass
        assert exc_info.value.code == 1008


@pytest.mark.anyio
async def test_websocket_order_authorization(client: AsyncClient):
    """Khách hàng không được phép lắng nghe kênh đơn hàng của người khác."""
    # 1. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_auth@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Auth",
        "role": "MERCHANT"
    })
    m_login = await client.post("/api/v1/auth/login", json={
        "email": "merchant_auth@foodhub.com",
        "password": "password123"
    })
    m_token = m_login.json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Pho Hanoi WS",
        "address": "123 Hang Gai",
        "latitude": 21.0313,
        "longitude": 105.8516
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Pho Bo Tai",
        "base_price": 45000,
        "stock_quantity": 20,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer A và Customer B
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_a@foodhub.com",
        "password": "password123",
        "full_name": "Customer A",
        "role": "CUSTOMER"
    })
    token_a = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_a@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    await client.post("/api/v1/auth/register", json={
        "email": "buyer_b@foodhub.com",
        "password": "password123",
        "full_name": "Customer B",
        "role": "CUSTOMER"
    })
    token_b = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_b@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    # Customer A đặt đơn
    order_res = await client.post(
        "/api/v1/orders/",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "restaurant_id": rest_id,
            "items": [{"menu_item_id": item_id, "quantity": 1}],
            "delivery_lat": 21.0300,
            "delivery_lng": 105.8500,
            "delivery_address": "45 Hoan Kiem"
        }
    )
    order_id = order_res.json()["id"]

    with TestClient(app) as tc:
        # Customer B cố gắng xem đơn của Customer A -> Từ chối 1008 (Forbidden)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect(f"/ws/orders/{order_id}?token={token_b}"):
                pass
        assert exc_info.value.code == 1008

        # Xem đơn không tồn tại -> Từ chối 1008 (Order not found)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect(f"/ws/orders/999999?token={token_a}"):
                pass
        assert exc_info.value.code == 1008


@pytest.mark.anyio
async def test_websocket_order_realtime_status_flow(client: AsyncClient):
    """Kiểm tra luồng kết nối đơn hàng, ping/pong và nhận thông báo đổi trạng thái."""
    # 1. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_flow@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Flow",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_flow@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Bun Bo Hue Realtime",
        "address": "12 Le Loi",
        "latitude": 16.4637,
        "longitude": 107.5909
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Bun Bo Dac Biet",
        "base_price": 55000,
        "stock_quantity": 15,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer & đặt đơn
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_flow@foodhub.com",
        "password": "password123",
        "full_name": "Customer Flow",
        "role": "CUSTOMER"
    })
    token_c = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_flow@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    order_res = await client.post(
        "/api/v1/orders/",
        headers={"Authorization": f"Bearer {token_c}"},
        json={
            "restaurant_id": rest_id,
            "items": [{"menu_item_id": item_id, "quantity": 1}],
            "delivery_lat": 16.4600,
            "delivery_lng": 107.5900,
            "delivery_address": "88 Hung Vuong"
        }
    )
    order_id = order_res.json()["id"]

    # 3. Kết nối WebSocket với tư cách Customer
    with TestClient(app) as tc:
        with tc.websocket_connect(f"/ws/orders/{order_id}?token={token_c}") as ws:
            # Nhận message khởi tạo
            init_msg = ws.receive_json()
            assert init_msg["event"] == "CONNECTED"
            assert init_msg["order_id"] == order_id
            assert init_msg["current_status"] == "SUBMITTED"

            # Kiểm tra ping/pong keep-alive
            ws.send_text("ping")
            pong = ws.receive_text()
            assert pong == "pong"

            # Quán cập nhật trạng thái đơn sang MERCHANT_ACCEPTED
            patch_res = tc.patch(
                f"/api/v1/orders/{order_id}/status",
                headers=m_headers,
                json={"new_status": "MERCHANT_ACCEPTED", "reason": "Quán đã nhận đơn"}
            )
            assert patch_res.status_code == 200

            # WebSocket nhận event ORDER_STATUS_CHANGED realtime
            status_msg = ws.receive_json()
            assert status_msg["event"] == "ORDER_STATUS_CHANGED"
            assert status_msg["order_id"] == order_id
            assert status_msg["old_status"] == "SUBMITTED"
            assert status_msg["new_status"] == "MERCHANT_ACCEPTED"


@pytest.mark.anyio
async def test_websocket_merchant_feed_flow(client: AsyncClient):
    """Kiểm tra feed đơn mới cho quán ăn qua /ws/merchant/feed."""
    # 1. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_feed@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Feed",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_feed@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Banh Mi Cha Ca Feed",
        "address": "50 Nguyen Trai",
        "latitude": 10.7680,
        "longitude": 106.6870
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Banh Mi Cha Ca",
        "base_price": 25000,
        "stock_quantity": 50,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer
    await client.post("/api/v1/auth/register", json={
        "email": "customer_for_feed@foodhub.com",
        "password": "password123",
        "full_name": "Customer Feed",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "customer_for_feed@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    with TestClient(app) as tc:
        # Customer cố kết nối vào merchant feed -> Bị từ chối 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect(f"/ws/merchant/feed?token={c_token}"):
                pass
        assert exc_info.value.code == 1008

        # Quán kết nối vào merchant feed -> Thành công
        with tc.websocket_connect(f"/ws/merchant/feed?token={m_token}") as ws:
            feed_init = ws.receive_json()
            assert feed_init["event"] == "CONNECTED"
            assert feed_init["restaurant_id"] == rest_id

            # Customer đặt đơn mới
            order_res = tc.post(
                "/api/v1/orders/",
                headers=c_headers,
                json={
                    "restaurant_id": rest_id,
                    "items": [{"menu_item_id": item_id, "quantity": 2}],
                    "delivery_lat": 10.7690,
                    "delivery_lng": 106.6880,
                    "delivery_address": "99 Nguyen Trai"
                }
            )
            assert order_res.status_code == 201
            order_data = order_res.json()

            # Quán nhận được thông báo NEW_ORDER realtime trên WebSocket
            feed_msg = ws.receive_json()
            assert feed_msg["event"] == "NEW_ORDER"
            assert feed_msg["order_id"] == order_data["id"]
            assert feed_msg["restaurant_id"] == rest_id


@pytest.mark.anyio
async def test_driver_location_update_and_ws_broadcast(client: AsyncClient, db_session: AsyncSession):
    """Kiểm tra tài xế gửi GPS và khách hàng nhận được toạ độ qua WebSocket đơn hàng."""
    # 1. Tạo Merchant & Quán
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_gps@foodhub.com",
        "password": "password123",
        "full_name": "Merchant GPS",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_gps@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Tra Sua Tran Chau GPS",
        "address": "100 Vo Van Tan",
        "latitude": 10.7745,
        "longitude": 106.6890
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Tra Sua Oolong",
        "base_price": 30000,
        "stock_quantity": 30,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 2. Tạo Customer & đặt đơn
    await client.post("/api/v1/auth/register", json={
        "email": "customer_gps@foodhub.com",
        "password": "password123",
        "full_name": "Customer GPS",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "customer_gps@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    order_res = await client.post(
        "/api/v1/orders/",
        headers={"Authorization": f"Bearer {c_token}"},
        json={
            "restaurant_id": rest_id,
            "items": [{"menu_item_id": item_id, "quantity": 1}],
            "delivery_lat": 10.7760,
            "delivery_lng": 106.6900,
            "delivery_address": "200 CMT8"
        }
    )
    order_id = order_res.json()["id"]

    # 3. Tạo Driver và gán đơn hàng
    await client.post("/api/v1/auth/register", json={
        "email": "driver_gps@foodhub.com",
        "password": "password123",
        "full_name": "Shipper Anh Tuan",
        "role": "DRIVER"
    })
    d_token = (await client.post("/api/v1/auth/login", json={
        "email": "driver_gps@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    d_headers = {"Authorization": f"Bearer {d_token}"}

    # Tạo DriverProfile & DriverAssignment trong DB
    driver_user = (await db_session.execute(select(User).where(User.email == "driver_gps@foodhub.com"))).scalar_one()
    profile = DriverProfile(
        user_id=driver_user.id,
        license_plate="59-P1 99999",
        current_lat=10.7700,
        current_lng=106.6800,
        is_online=True,
        is_busy=True
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    assignment = DriverAssignment(
        order_id=order_id,
        driver_id=profile.id,
        status=DriverAssignmentStatus.DELIVERING
    )
    db_session.add(assignment)
    await db_session.commit()

    # 4. Khách hàng theo dõi đơn qua WebSocket, Tài xế bắn GPS
    with TestClient(app) as tc:
        with tc.websocket_connect(f"/ws/orders/{order_id}?token={c_token}") as ws:
            init_msg = ws.receive_json()
            assert init_msg["event"] == "CONNECTED"

            # Tài xế cập nhật toạ độ GPS
            gps_res = tc.post(
                "/api/v1/driver/location",
                headers=d_headers,
                json={"latitude": 10.774512, "longitude": 106.689034}
            )
            assert gps_res.status_code == 200
            assert gps_res.json()["active_orders"] == 1

            # Khách hàng nhận được toạ độ GPS qua WebSocket realtime
            gps_msg = ws.receive_json()
            assert gps_msg["event"] == "DRIVER_LOCATION_UPDATED"
            assert gps_msg["order_id"] == order_id
            assert gps_msg["driver_id"] == driver_user.id
            assert gps_msg["latitude"] == 10.774512
            assert gps_msg["longitude"] == 106.689034


@pytest.mark.anyio
async def test_admin_live_ops_ws_forbidden_for_non_admin(client: AsyncClient):
    """Kiểm tra chỉ có ADMIN mới được phép kết nối /ws/admin/live-ops."""
    # 1. Tạo Customer & Merchant
    await client.post("/api/v1/auth/register", json={
        "email": "customer_ops@foodhub.com",
        "password": "password123",
        "full_name": "Customer Ops",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "customer_ops@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    await client.post("/api/v1/auth/register", json={
        "email": "merchant_ops@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Ops",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_ops@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    with TestClient(app) as tc:
        # Không có token -> 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/ws/admin/live-ops"):
                pass
        assert exc_info.value.code == 1008

        # Customer token -> 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect(f"/ws/admin/live-ops?token={c_token}"):
                pass
        assert exc_info.value.code == 1008

        # Merchant token -> 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect(f"/ws/admin/live-ops?token={m_token}"):
                pass
        assert exc_info.value.code == 1008


@pytest.mark.anyio
async def test_admin_live_ops_ws_connect_snapshot_and_ping_pong(client: AsyncClient):
    """Kiểm tra Admin kết nối nhận INITIAL_SNAPSHOT, ping/pong và refresh."""
    # 1. Tạo Admin
    await client.post("/api/v1/auth/register", json={
        "email": "admin_liveops@foodhub.com",
        "password": "password123",
        "full_name": "Admin LiveOps",
        "role": "ADMIN"
    })
    admin_token = (await client.post("/api/v1/auth/login", json={
        "email": "admin_liveops@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    with TestClient(app) as tc:
        with tc.websocket_connect(f"/ws/admin/live-ops?token={admin_token}") as ws:
            # 2. Nhận INITIAL_SNAPSHOT
            init_msg = ws.receive_json()
            assert init_msg["event"] == "INITIAL_SNAPSHOT"
            assert init_msg["channel"] == "admin:live_ops"
            data = init_msg["data"]
            assert "active_orders_count" in data
            assert "active_orders_by_status" in data
            assert "total_delivered_revenue" in data
            assert "online_drivers_count" in data
            assert "busy_drivers_count" in data
            assert "active_websocket_connections" in data

            # 3. Ping / Pong
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

            # 4. Refresh snapshot
            ws.send_text("refresh")
            refresh_msg = ws.receive_json()
            assert refresh_msg["event"] == "METRICS_UPDATE"
            assert "active_orders_count" in refresh_msg["data"]


@pytest.mark.anyio
async def test_admin_live_ops_receives_realtime_order_broadcast(client: AsyncClient):
    """Kiểm tra Admin Live-Ops nhận broadcast sự kiện khi có đơn hàng mới hoặc đổi trạng thái."""
    # 1. Tạo Admin
    await client.post("/api/v1/auth/register", json={
        "email": "super_admin_ops@foodhub.com",
        "password": "password123",
        "full_name": "Super Admin Ops",
        "role": "ADMIN"
    })
    admin_token = (await client.post("/api/v1/auth/login", json={
        "email": "super_admin_ops@foodhub.com",
        "password": "password123"
    })).json()["access_token"]

    # 2. Tạo Merchant & Nhà hàng
    await client.post("/api/v1/auth/register", json={
        "email": "merchant_ops_live@foodhub.com",
        "password": "password123",
        "full_name": "Merchant Live",
        "role": "MERCHANT"
    })
    m_token = (await client.post("/api/v1/auth/login", json={
        "email": "merchant_ops_live@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    m_headers = {"Authorization": f"Bearer {m_token}"}

    rest_res = await client.post("/api/v1/restaurants/", headers=m_headers, json={
        "name": "Pho Thin Realtime LiveOps",
        "address": "13 Lo Duc",
        "latitude": 21.0180,
        "longitude": 105.8560
    })
    rest_id = rest_res.json()["id"]

    item_res = await client.post(f"/api/v1/menu/restaurants/{rest_id}/items", headers=m_headers, json={
        "name": "Pho Tai Lan",
        "base_price": 60000,
        "stock_quantity": 25,
        "is_available": True
    })
    item_id = item_res.json()["id"]

    # 3. Tạo Customer
    await client.post("/api/v1/auth/register", json={
        "email": "buyer_ops_live@foodhub.com",
        "password": "password123",
        "full_name": "Buyer Ops Live",
        "role": "CUSTOMER"
    })
    c_token = (await client.post("/api/v1/auth/login", json={
        "email": "buyer_ops_live@foodhub.com",
        "password": "password123"
    })).json()["access_token"]
    c_headers = {"Authorization": f"Bearer {c_token}"}

    with TestClient(app) as tc:
        # Admin kết nối vào /ws/admin/live-ops
        with tc.websocket_connect(f"/ws/admin/live-ops?token={admin_token}") as ws:
            init_msg = ws.receive_json()
            assert init_msg["event"] == "INITIAL_SNAPSHOT"

            # Customer đặt đơn mới
            order_res = tc.post(
                "/api/v1/orders/",
                headers=c_headers,
                json={
                    "restaurant_id": rest_id,
                    "items": [{"menu_item_id": item_id, "quantity": 1}],
                    "delivery_lat": 21.0200,
                    "delivery_lng": 105.8570,
                    "delivery_address": "99 Lo Duc"
                }
            )
            assert order_res.status_code == 201
            order_id = order_res.json()["id"]

            # Admin Live-Ops nhận broadcast ORDER_CREATED ngay tức thì
            order_created_msg = ws.receive_json()
            assert order_created_msg["event"] == "ORDER_CREATED"
            assert order_created_msg["order_id"] == order_id
            assert order_created_msg["restaurant_id"] == rest_id

            # Merchant duyệt đơn (MERCHANT_ACCEPTED)
            status_res = tc.patch(
                f"/api/v1/orders/{order_id}/status",
                headers=m_headers,
                json={"new_status": "MERCHANT_ACCEPTED", "reason": "Nhà hàng nhận đơn"}
            )
            assert status_res.status_code == 200

            # Admin Live-Ops nhận broadcast ORDER_STATUS_CHANGED ngay tức thì
            status_changed_msg = ws.receive_json()
            assert status_changed_msg["event"] == "ORDER_STATUS_CHANGED"
            assert status_changed_msg["order_id"] == order_id
            assert status_changed_msg["new_status"] == "MERCHANT_ACCEPTED"


