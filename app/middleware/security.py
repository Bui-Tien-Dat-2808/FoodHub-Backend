from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware tự động đính kèm các HTTP Security Headers chuẩn OWASP vào mọi HTTP Response trả về cho client.
    """
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Chống Clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Chống MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Chống XSS trên trình duyệt cũ
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Ép HTTPS (HSTS) trong 1 năm kèm subdomains
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Bảo vệ thông tin Referrer
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Giới hạn quyền truy cập phần cứng thiết bị từ browser
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # Xoá header lộ thông tin công nghệ (nếu có)
        if "Server" in response.headers:
            del response.headers["Server"]

        return response