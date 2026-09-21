import logging
import time

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)

@celery_app.task(name="app.tasks.notification_tasks.send_order_status_notification")
def send_order_status_notification(order_id: int, new_status: str, recipient: str) -> dict:
    """Gửi Email/SMS thông báo khi trạng thái đơn hàng thay đổi"""
    time.sleep(0.05)

    msg = f"Trạng thái đơn hàng #{order_id} hiện tại của bạn: {new_status}. Hiện tại đã tới {recipient}"
    logger.info(msg)

    return {
        "order_id": order_id,
        "new_status": new_status,
        "recipient": recipient,
        "delivered": True,
    }