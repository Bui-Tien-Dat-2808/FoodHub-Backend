import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_security_headers_present_on_responses(client: AsyncClient):
    """Kiểm tra các HTTP Security Headers theo tiêu chuẩn OWASP được đính kèm vào response."""
    response = await client.get("/health")
    assert response.status_code == 200

    headers = response.headers

    # 1. Chống Clickjacking
    assert headers.get("x-frame-options") == "DENY"

    # 2. Chống MIME-sniffing
    assert headers.get("x-content-type-options") == "nosniff"

    # 3. Chống XSS trên trình duyệt cũ
    assert headers.get("x-xss-protection") == "1; mode=block"

    # 4. Ép buộc HTTPS (HSTS)
    assert headers.get("strict-transport-security") == "max-age=31536000; includeSubDomains"

    # 5. Referrer Policy
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    # 6. Permissions Policy
    assert "permissions-policy" in headers

    # 7. Ẩn header lộ thông tin Server
    assert "server" not in headers

