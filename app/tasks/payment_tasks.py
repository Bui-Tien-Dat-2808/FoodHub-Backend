import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


class TransientPaymentGatewayError(Exception):
    """Lỗi tạm khi kết nối cổng thanh toán (timeout, network drop)"""
    pass


@celery_app.task(
    name="app.tasks.payment_tasks.process_mock_payment_with_retry",
    bind=True,
    autoretry_for=(TransientPaymentGatewayError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def process_mock_payment_with_retry(
    self, order_id: int, amount: int, simulate_failure_count: int = 0
) -> dict:
    """Giả lập xử lý thanh toán với cơ chế retry khi gặp sự cố."""
    attempt = self.request.retries
    logger.info(f"Xử lý thanh toán đơn #{order_id}, số tiền: {amount}đ (Lần thử {attempt + 1})")

    # Giả lập lỗi mạng tạm thời nếu chưa đủ số lần yêu cầu
    if attempt < simulate_failure_count:
        logger.warning(f"Gặp sự cố mạng ở lần thử {attempt + 1}. Đang chuẩn bị retry...")
        raise TransientPaymentGatewayError(
            f"Cổng thanh toán phản hồi timeout ở lần thử {attempt + 1}"
        )

    logger.info(f"Thanh toán thành công cho đơn #{order_id}")
    return {
        "order_id": order_id,
        "amount": amount,
        "status": "SUCCESS",
        "retries_count": attempt,
    }