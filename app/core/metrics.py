import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# 1. Các định nghĩa Metrics chuẩn Prometheus
HTTP_REQUESTS_TOTAL = Counter(
    "foodhub_http_requests_total",
    "Tổng số lượng HTTP requests đến hệ thống",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "foodhub_http_request_duration_seconds",
    "Thời gian xử lý HTTP request tính bằng giây",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

ACTIVE_WEBSOCKET_CONNECTIONS = Gauge(
    "foodhub_active_websocket_connections",
    "Số kết nối WebSocket realtime đang mở đồng thời",
    ["channel"],
)

ACTIVE_ORDERS_COUNT = Gauge(
    "foodhub_active_orders_count",
    "Số đơn hàng đang trong quá trình xử lý (chưa hoàn thành/huỷ)",
)

ORDERS_CREATED_TOTAL = Counter(
    "foodhub_orders_created_total",
    "Tổng số lượng đơn hàng đã được tạo thành công",
)

ORDERS_STATUS_CHANGED_TOTAL = Counter(
    "foodhub_orders_status_changed_total",
    "Số lần chuyển đổi trạng thái đơn hàng trong hệ thống",
    ["status"],
)


def get_metrics_response() -> Response:
    """Trả về dữ liệu text format chuẩn Prometheus cho scraper."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware tự động đo lường số lượng request và độ trễ (latency)
    của từng API endpoint.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Bỏ qua đo đạc chính endpoint /metrics để tránh sai lệch thống kê
        path = request.url.path
        if path == "/metrics":
            return await call_next(request)

        method = request.method
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            status_code = str(response.status_code)
        except Exception:
            status_code = "500"
            raise
        finally:
            duration = time.perf_counter() - start_time
            # Chuẩn hóa đường dẫn: lấy route path nếu có để gom nhóm /orders/{id}
            endpoint = path
            if hasattr(request, "scope") and "route" in request.scope:
                route = request.scope.get("route")
                if route and hasattr(route, "path"):
                    endpoint = route.path

            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                endpoint=endpoint,
                status_code=status_code,
            ).inc()

            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

        return response

