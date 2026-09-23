"""Tests for Direction 3: Tiered Rate Limiting & Circuit Breaker / Resilience.

Validates:
1. Sliding Window Rate Limiter using Redis: accurate request counting and IETF headers (Retry-After, X-RateLimit-*).
2. Tiered Rate Limiting: role-based quota differentiation (CUSTOMER, DRIVER, MERCHANT, ADMIN bypass).
3. Circuit Breaker CLOSED -> OPEN transition on consecutive failures, fast-failing with HTTP 503.
4. Circuit Breaker graceful fallback execution when OPEN.
5. Circuit Breaker OPEN -> HALF_OPEN -> CLOSED recovery flow with canary request.
6. Circuit Breaker canary failure immediately reverting to OPEN.
7. Admin Resilience APIs: monitoring snapshots and manual reset.
"""

import asyncio

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
    circuit_registry,
)
from app.core.rate_limiter import (
    check_sliding_window_rate_limit,
    check_tiered_rate_limit,
)
from app.models.enums import UserRole


@pytest.mark.anyio
async def test_sliding_window_rate_limiter(fake_redis):
    """Kiểm tra thuật toán Sliding Window Rate Limiter với Redis ZSET."""
    key = "test_ratelimit:sliding:sample_user"
    await fake_redis.delete(key)

    # Giới hạn 3 req/60s
    res1 = await check_sliding_window_rate_limit(fake_redis, key, max_requests=3, window_seconds=60)
    assert res1["limit"] == 3
    assert res1["remaining"] == 2

    res2 = await check_sliding_window_rate_limit(fake_redis, key, max_requests=3, window_seconds=60)
    assert res2["remaining"] == 1

    res3 = await check_sliding_window_rate_limit(fake_redis, key, max_requests=3, window_seconds=60)
    assert res3["remaining"] == 0

    # Lần thứ 4 vượt hạn ngạch -> Ném HTTPException 429
    with pytest.raises(HTTPException) as exc_info:
        await check_sliding_window_rate_limit(fake_redis, key, max_requests=3, window_seconds=60)

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    assert exc_info.value.headers["X-RateLimit-Limit"] == "3"
    assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.anyio
async def test_tiered_rate_limiter_by_role(fake_redis):
    """Kiểm tra phân cấp hạn ngạch theo vai trò (Customer 30, Merchant 120, Admin không giới hạn)."""
    # 1. ADMIN bypass
    admin_info = await check_tiered_rate_limit(
        redis=fake_redis,
        identifier="admin_1",
        role=UserRole.ADMIN.value,
        resource="test_resource",
    )
    assert admin_info["limit"] == 999999
    assert "ADMIN" in admin_info["tier"]

    # 2. CUSTOMER limit = 30
    cust_info = await check_tiered_rate_limit(
        redis=fake_redis,
        identifier="cust_1",
        role=UserRole.CUSTOMER.value,
        resource="test_cust",
    )
    assert cust_info["limit"] == 30
    assert cust_info["tier"] == UserRole.CUSTOMER.value

    # 3. MERCHANT limit = 120
    merch_info = await check_tiered_rate_limit(
        redis=fake_redis,
        identifier="merch_1",
        role=UserRole.MERCHANT.value,
        resource="test_merch",
    )
    assert merch_info["limit"] == 120
    assert merch_info["tier"] == UserRole.MERCHANT.value

    # 4. DRIVER limit = 60
    driver_info = await check_tiered_rate_limit(
        redis=fake_redis,
        identifier="driver_1",
        role=UserRole.DRIVER.value,
        resource="test_driver",
    )
    assert driver_info["limit"] == 60
    assert driver_info["tier"] == UserRole.DRIVER.value


@pytest.mark.anyio
async def test_circuit_breaker_closed_to_open(fake_redis):
    """Kiểm tra Circuit Breaker ngắt mạch (CLOSED -> OPEN) khi số lỗi liên tiếp đạt ngưỡng."""
    cb = CircuitBreaker(name="test_payment_gw", failure_threshold=2, recovery_timeout=5.0)
    await cb.reset(fake_redis)

    assert await cb.get_state(fake_redis) == CircuitState.CLOSED

    call_count = 0

    async def faulty_payment_call():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("Payment provider timeout")

    # Lần 1: Lỗi -> Vẫn CLOSED (1/2 lỗi)
    with pytest.raises(ConnectionError):
        await cb.execute(faulty_payment_call, redis=fake_redis)
    assert await cb.get_state(fake_redis) == CircuitState.CLOSED
    assert call_count == 1

    # Lần 2: Lỗi -> Đạt ngưỡng 2/2 -> Chuyển sang OPEN
    with pytest.raises(ConnectionError):
        await cb.execute(faulty_payment_call, redis=fake_redis)
    assert await cb.get_state(fake_redis) == CircuitState.OPEN
    assert call_count == 2

    # Lần 3: Mạch OPEN -> Fail Fast: Ném CircuitBreakerOpenException (HTTP 503) mà KHÔNG gọi hàm đích
    with pytest.raises(CircuitBreakerOpenException) as exc_info:
        await cb.execute(faulty_payment_call, redis=fake_redis)
    assert exc_info.value.status_code == 503
    assert call_count == 2  # Hàm không bị gọi thêm lần nào!


@pytest.mark.anyio
async def test_circuit_breaker_with_fallback(fake_redis):
    """Kiểm tra Circuit Breaker thực thi Fallback khi mạch OPEN mà không làm sập request."""
    cb = CircuitBreaker(name="test_sms_gw", failure_threshold=1, recovery_timeout=10.0)
    await cb.reset(fake_redis)

    # Gây lỗi để ngắt mạch
    async def bad_call():
        raise RuntimeError("SMS provider down")

    with pytest.raises(RuntimeError):
        await cb.execute(bad_call, redis=fake_redis)

    assert await cb.get_state(fake_redis) == CircuitState.OPEN

    # Gọi với fallback
    async def fallback_send(msg: str):
        return {"status": "QUEUED_OFFLINE", "message": msg}

    result = await cb.execute(bad_call, "Xin chao", fallback=fallback_send, redis=fake_redis)
    assert result["status"] == "QUEUED_OFFLINE"
    assert result["message"] == "Xin chao"


@pytest.mark.anyio
async def test_circuit_breaker_open_to_half_open_to_closed(fake_redis):
    """Kiểm tra chu trình tự phục hồi: OPEN -> Hết timeout -> HALF_OPEN -> Thử nghiệm thành công -> CLOSED."""
    cb = CircuitBreaker(name="test_recovery_gw", failure_threshold=1, recovery_timeout=0.2)
    await cb.reset(fake_redis)

    # Gây lỗi -> OPEN
    async def bad_call():
        raise ValueError("Service error")

    with pytest.raises(ValueError):
        await cb.execute(bad_call, redis=fake_redis)
    assert await cb.get_state(fake_redis) == CircuitState.OPEN

    # Chờ 0.25s để vượt qua recovery_timeout
    await asyncio.sleep(0.25)

    # Trạng thái tự động đổi thành HALF_OPEN
    state = await cb.get_state(fake_redis)
    assert state == CircuitState.HALF_OPEN

    # Canary request thành công
    async def good_call():
        return "SUCCESS"

    result = await cb.execute(good_call, redis=fake_redis)
    assert result == "SUCCESS"

    # Mạch đóng lại hoàn toàn (CLOSED)
    assert await cb.get_state(fake_redis) == CircuitState.CLOSED


@pytest.mark.anyio
async def test_circuit_breaker_half_open_failure_reopens(fake_redis):
    """Kiểm tra Canary request thất bại ở trạng thái HALF_OPEN lập tức đưa mạch quay lại OPEN."""
    cb = CircuitBreaker(name="test_half_open_fail", failure_threshold=1, recovery_timeout=0.2)
    await cb.reset(fake_redis)

    # Gây lỗi -> OPEN
    async def bad_call():
        raise ConnectionResetError("Remote server down")

    with pytest.raises(ConnectionResetError):
        await cb.execute(bad_call, redis=fake_redis)
    assert await cb.get_state(fake_redis) == CircuitState.OPEN

    # Chờ 0.25s -> HALF_OPEN
    await asyncio.sleep(0.25)
    assert await cb.get_state(fake_redis) == CircuitState.HALF_OPEN

    # Canary request tiếp tục thất bại -> Lập tức quay lại OPEN
    with pytest.raises(ConnectionResetError):
        await cb.execute(bad_call, redis=fake_redis)

    assert await cb.get_state(fake_redis) == CircuitState.OPEN


@pytest.mark.anyio
async def test_admin_resilience_api(client: AsyncClient):
    """Kiểm tra API Quản trị viên giám sát Circuit Breakers và quyền can thiệp reset mạch."""
    # 1. Đăng ký tài khoản Admin
    await client.post(
        "/api/v1/auth/register",
        json={"email": "admin_resil@foodhub.com", "password": "password123", "full_name": "Admin Resilience", "role": "ADMIN"},
    )
    admin_token = (
        await client.post("/api/v1/auth/login", json={"email": "admin_resil@foodhub.com", "password": "password123"})
    ).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Admin xem danh sách Circuit Breakers
    res = await client.get("/api/v1/admin/resilience/circuit-breakers", headers=admin_headers)
    assert res.status_code == 200
    breakers = res.json()
    names = [b["name"] for b in breakers]
    assert "payment_gateway" in names
    assert "sms_notification" in names
    assert "external_maps" in names

    # 3. Kích hoạt lỗi trên payment_gateway
    pg_breaker = circuit_registry.get("payment_gateway")
    assert pg_breaker is not None
    # Ghi nhận lỗi nhân tạo để làm ngắt mạch
    for _ in range(pg_breaker.failure_threshold):
        await pg_breaker.record_failure()

    # Kiểm tra trạng thái hiện tại là OPEN
    snap = await pg_breaker.get_snapshot()
    assert snap["state"] == "OPEN"

    # 4. Admin gọi API reset mạch về CLOSED
    reset_res = await client.post("/api/v1/admin/resilience/circuit-breakers/payment_gateway/reset", headers=admin_headers)
    assert reset_res.status_code == 200
    assert reset_res.json()["status"] == "success"
    assert reset_res.json()["snapshot"]["state"] == "CLOSED"
    assert reset_res.json()["snapshot"]["failure_count"] == 0
