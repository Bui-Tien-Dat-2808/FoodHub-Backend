import time
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException, status

from app.models.enums import UserRole

# Định nghĩa hạn ngạch (quota) theo cấp bậc người dùng (số request / 60 giây)
DEFAULT_TIER_LIMITS: dict[str, int] = {
    "ANONYMOUS": 10,  # Chưa đăng nhập (theo IP)
    UserRole.CUSTOMER.value: 30,  # Khách hàng đặt món
    UserRole.DRIVER.value: 60,  # Tài xế gửi vị trí / cập nhật chặng
    UserRole.MERCHANT.value: 120,  # Nhà hàng quản lý thực đơn / đơn hàng
    UserRole.ADMIN.value: 1000,  # Quản trị viên hệ thống
}


async def check_rate_limit(
    redis: aioredis.Redis,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    """
        Fixed Window Counter Rate Limiter:
        - key: Chuỗi định danh (VD: 'ratelimit:user:1:create_order' hoặc 'ratelimit:ip:127.0.0.1:login')
        - max_requests: Số lượng request tối đa trong window
        - window_seconds: Độ dài cửa sổ thời gian (giây)
    Fixed Window Counter Rate Limiter (Bảo toàn tương thích ngược 100%):
    - key: Chuỗi định danh (VD: 'ratelimit:user:1:create_order' hoặc 'ratelimit:ip:127.0.0.1:login')
    - max_requests: Số lượng request tối đa trong window
    - window_seconds: Độ dài cửa sổ thời gian (giây)
    """
    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)

        if current > max_requests:
            ttl = await redis.ttl(key)
            ttl = max(1, ttl)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Bạn thao tác quá nhanh. Vui lòng thử lại sau {ttl} giây.",
                headers={"Retry-After": str(ttl)},
            )
    except aioredis.RedisError:
        pass


async def check_sliding_window_rate_limit(
    redis: aioredis.Redis,
    key: str,
    max_requests: int,
    window_seconds: int = 60,
) -> dict[str, int]:
    """
    Sliding Window Rate Limiter bằng Redis Sorted Set (ZSET):
    - Loại bỏ hiện tượng traffic spike tại biên của Fixed Window.
    - Trả về thông tin hạn ngạch IETF: Limit, Remaining, Reset.
    """
    now = time.time()
    clear_before = now - window_seconds
    unique_member = f"{now:.6f}:{uuid4().hex[:6]}"

    try:
        pipe = redis.pipeline()
        # 1. Xóa các mốc thời gian ngoài cửa sổ trượt
        pipe.zremrangebyscore(key, 0, clear_before)
        # 2. Đếm số lượng request hiện tại trong cửa sổ
        pipe.zcard(key)
        # 3. Lấy request cũ nhất trong cửa sổ để tính thời gian reset chính xác
        pipe.zrange(key, 0, 0, withscores=True)
        results = await pipe.execute()

        current_count = results[1]
        oldest_entry = results[2]

        if current_count >= max_requests:
            oldest_time = oldest_entry[0][1] if oldest_entry else (now - window_seconds)
            retry_after = max(1, int(oldest_time + window_seconds - now))

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Vượt quá giới hạn tần suất ({max_requests} yêu cầu/{window_seconds}s). Thử lại sau {retry_after}s.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        # Chưa vượt hạn ngạch: Thêm request hiện tại vào ZSET và gia hạn TTL
        pipe2 = redis.pipeline()
        pipe2.zadd(key, {unique_member: now})
        pipe2.expire(key, window_seconds + 5)
        await pipe2.execute()

        remaining = max(0, max_requests - (current_count + 1))
        return {
            "limit": max_requests,
            "remaining": remaining,
            "reset": window_seconds,
        }
    except aioredis.RedisError:
        # Nếu Redis gặp sự cố, fail-open an toàn không chặn người dùng
        return {
            "limit": max_requests,
            "remaining": max_requests,
            "reset": window_seconds,
        }


async def check_tiered_rate_limit(
    redis: aioredis.Redis,
    identifier: str,
    role: str | None = None,
    resource: str = "general",
    window_seconds: int = 60,
    custom_limit: int | None = None,
) -> dict[str, Any]:
    """
    Kiểm tra giới hạn tần suất phân cấp theo vai trò (Tiered Rate Limiter):
    - role == ADMIN: Không giới hạn (hoặc trần rất cao).
    - role == MERCHANT: 120 req/min.
    - role == DRIVER: 60 req/min.
    - role == CUSTOMER: 30 req/min.
    - ANONYMOUS: 10 req/min.
    """
    # Nếu là ADMIN và không chỉ định custom_limit -> bypass không giới hạn
    if role == UserRole.ADMIN.value and custom_limit is None:
        return {
            "limit": 999999,
            "remaining": 999999,
            "reset": 0,
            "tier": "ADMIN (Unlimited)",
        }

    tier_name = role if role in DEFAULT_TIER_LIMITS else "ANONYMOUS"
    limit = custom_limit if custom_limit is not None else DEFAULT_TIER_LIMITS[tier_name]

    key = f"ratelimit:sliding:{resource}:{identifier}"
    rate_info = await check_sliding_window_rate_limit(
        redis=redis,
        key=key,
        max_requests=limit,
        window_seconds=window_seconds,
    )
    rate_info["tier"] = tier_name
    return rate_info