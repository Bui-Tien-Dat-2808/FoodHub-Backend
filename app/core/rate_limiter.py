import redis.asyncio as aioredis
from fastapi import HTTPException, status


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