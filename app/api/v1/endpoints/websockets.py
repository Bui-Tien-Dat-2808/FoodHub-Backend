import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models.enums import UserRole
from app.models.order import Order
from app.models.restaurant import Restaurant
from app.models.user import User
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

async def get_current_user_ws(
    websocket: WebSocket,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> User | None:
    """
        Xác thực JWT Token cho WebSocket.
        Nếu không hợp lệ, đóng socket với mã WS_1008_POLICY_VIOLATION
    """
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return None

    payload = decode_token(token)
    if not payload or "sub" not in payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return None

    user_id = payload["sub"]
    stmt = select(User).where(User.id == int(user_id))
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user or not user.is_active:
        await db.rollback()
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="User not active")
        return None

    return user

@router.websocket("/orders/{order_id}")
async def order_realtime_status(
    websocket: WebSocket,
    order_id: int,
    token: str | None = Query(None),
    db: Annotated[AsyncSession, Depends(get_db)] = None
):
    """
        WebSocket cho Khách hàng / Quán / Tài xế theo dõi:
        - Trạng thái đơn hàng
        - Vị trí GPS của tài xế
    """
    current_user = await get_current_user_ws(websocket, token, db)
    if not current_user:
        return

    # 1. Kiểm tra đơn hàng tồn tại
    stmt = select(Order).where(Order.id == order_id)
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        await db.rollback()
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Order not found")
        return

    # 2. Phân quyền: Chỉ chủ đơn, quán sở hữu đơn, hoặc Admin mới được kết nối
    is_owner = order.customer_id == current_user.id
    is_admin = current_user.role == UserRole.ADMIN
    is_merchant = False
    if current_user.role == UserRole.MERCHANT:
        rest = (await db.execute(select(Restaurant).where(Restaurant.id == order.restaurant_id))).scalar_one_or_none()
        if rest and rest.owner_id == current_user.id:
            is_merchant = True

    if not (is_owner or is_admin or is_merchant):
        await db.rollback()
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden")
        return

    # 3. Chấp nhận kết nối và đăng ký vào channel
    channel = f"orders:{order_id}"
    await ws_manager.connect(websocket, channel)

    # Gửi message kèm trạng thái hiện tại
    await websocket.send_json({
        "event": "CONNECTED",
        "order_id": order_id,
        "current_status": order.status.value
    })

    # 4. Giữ kết nối
    # 4. Giải phóng DB session để không chiếm giữ connection/lock trong suốt vòng đời WebSocket
    await db.rollback()
    await db.close()

    # 5. Giữ kết nối
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, channel)

@router.websocket("/merchant/feed")
async def merchant_order_feed(
    websocket:  WebSocket,
    token: str | None = Query(None),
    db: Annotated[AsyncSession, Depends(get_db)] = None
):
    """
        Quán ăn nhận thông báo đơn mới ở đây.
    """
    current_user = await get_current_user_ws(websocket, token, db)
    if not current_user:
        return

    # Chỉ cho phép MERCHANT hoặc ADMIN
    if current_user.role not in (UserRole.MERCHANT, UserRole.ADMIN):
        await db.rollback()
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Merchant/Admin role required")
        return

    # Tìm nhà hàng
    stmt = select(Restaurant).where(Restaurant.owner_id == current_user.id)
    restaurant = (await db.execute(stmt)).scalar_one_or_none()
    if not restaurant:
        await db.rollback()
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Restaurant not found")
        return
    
    channel = f"merchant:{restaurant.id}"
    await ws_manager.connect(websocket, channel)

    await websocket.send_json({
        "event": "CONNECTED",
        "channel": channel,
        "restaurant_id": restaurant.id
    })

    # Giải phóng DB session để không chiếm giữ connection/lock trong suốt vòng đời WebSocket
    await db.rollback()
    await db.close()

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, channel)


@router.websocket("/admin/live-ops")
async def admin_live_ops_feed(
    websocket: WebSocket,
    token: str | None = Query(None),
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """
    WebSocket Realtime Dashboard cho Quản trị viên (Admin Live-Ops):
    - Kiểm soát phân quyền: Chỉ ADMIN được phép kết nối (đóng 1008 nếu vi phạm).
    - Cung cấp INITIAL_SNAPSHOT với số liệu vận hành toàn hệ thống.
    - Nhận lệnh 'ping' -> phản hồi 'pong' (heartbeat).
    - Nhận lệnh 'refresh' -> trả về METRICS_UPDATE mới nhất.
    - Nhận broadcast realtime các sự kiện phát sinh từ hệ thống qua channel 'admin:live_ops'.
    """
    current_user = await get_current_user_ws(websocket, token, db)
    if not current_user:
        return

    # Chỉ cho phép ADMIN
    if current_user.role != UserRole.ADMIN:
        await db.rollback()
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Admin role required")
        return

    channel = "admin:live_ops"
    await ws_manager.connect(websocket, channel)

    # 1. Tính toán & gửi Snapshot khởi tạo
    try:
        from app.services.live_ops_service import get_live_ops_snapshot
        snapshot = await get_live_ops_snapshot(db)
        await websocket.send_json({
            "event": "INITIAL_SNAPSHOT",
            "channel": channel,
            "data": snapshot,
        })
    except Exception as exc:
        logger.error(f"Lỗi khi gửi snapshot live-ops: {exc}")

    # 2. Giải phóng kết nối DB ban đầu
    await db.rollback()
    await db.close()

    # 3. Lắng nghe client (ping, refresh)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            elif data == "refresh":
                from app.main import app
                from app.services.live_ops_service import get_live_ops_snapshot
                db_dep = app.dependency_overrides.get(get_db, get_db)
                async for refresh_db in db_dep():
                    try:
                        latest_snapshot = await get_live_ops_snapshot(refresh_db)
                        await websocket.send_json({
                            "event": "METRICS_UPDATE",
                            "channel": channel,
                            "data": latest_snapshot,
                        })
                    finally:
                        await refresh_db.close()
                    break
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, channel)