import json
import logging
from collections import defaultdict
from typing import Any

import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

class WebSocketConnectionManager:
    """WebSocket cục bộ và Redis Pub/Sub"""
    def __init__(self):
        self.active_connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        """Kết nối WebSocket"""
        await websocket.accept()
        self.active_connections[channel].add(websocket)
        try:
            from app.core.metrics import ACTIVE_WEBSOCKET_CONNECTIONS
            ACTIVE_WEBSOCKET_CONNECTIONS.labels(channel=channel).inc()
        except Exception:
            pass
        logger.info(f"Client kết nối vào channel '{channel}'")

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        """Huỷ kết nối WebSocket"""
        if channel in self.active_connections:
            self.active_connections[channel].discard(websocket)
            if not self.active_connections[channel]:
                del self.active_connections[channel]
        try:
            from app.core.metrics import ACTIVE_WEBSOCKET_CONNECTIONS
            ACTIVE_WEBSOCKET_CONNECTIONS.labels(channel=channel).dec()
        except Exception:
            pass
        logger.info(f"Client rời channel '{channel}'")

    def get_total_connections_count(self) -> int:
        """Trả về tổng số kết nối WebSocket đang mở trên toàn bộ channels."""
        return sum(len(conns) for conns in self.active_connections.values())

    async def broadcast_local(self, channel: str, message: dict[str, Any]) -> None:
        """Gửi message tới các Client đang kết nối với WebSocket"""
        connections = self.active_connections.get(channel, set()).copy()
        dead_connections = set()

        for connection in connections:
            try:
                await connection.send_json(message)
            except (WebSocketDisconnect, Exception):
                dead_connections.add(connection)

        for dead in dead_connections:
            self.disconnect(dead, channel)

    async def publish(self, redis: aioredis.Redis, channel: str, message: dict[str, Any]) -> None:
        """Redis Pub/Sub"""

        # 1. Gửi cho các client cục bộ trên cùng worker
        await self.broadcast_local(channel, message)

        # 2. Đưa lên Redis Pub/Sub
        try:
            if hasattr(redis, "publish"):
                await redis.publish(channel, json.dumps(message))
        except Exception as e:
            logger.warning(f"Không thể publish tới '{channel}': {e}")

    async def broadcast_admin_live_ops(self, redis: aioredis.Redis, message: dict[str, Any]) -> None:
        """Phát sóng sự kiện realtime tới kênh Admin Live-Ops."""
        await self.publish(redis, "admin:live_ops", message)


ws_manager = WebSocketConnectionManager()