import inspect
import logging
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Mạch đóng bình thường, cho phép các request đi qua
    OPEN = "OPEN"            # Mạch ngắt, từ chối ngay lập tức không gọi dịch vụ bên ngoài
    HALF_OPEN = "HALF_OPEN"  # Mạch thử nghiệm, cho phép request canary thăm dò phục hồi


class CircuitBreakerOpenException(HTTPException):
    """Ngoại lệ trả về HTTP 503 khi mạch đang ngắt (OPEN) để bảo vệ hệ thống."""

    def __init__(self, circuit_name: str, retry_after: int = 15):
        detail = (
            f"Dịch vụ bên ngoài '{circuit_name}' tạm thời không khả dụng do hệ thống bảo vệ "
            f"đang ngắt mạch (Circuit Breaker OPEN). Vui lòng thử lại sau {retry_after} giây."
        )
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )


class CircuitBreaker:
    """
    Bộ ngắt mạch phân tán (Distributed Circuit Breaker) hỗ trợ đồng bộ trạng thái qua Redis
    và tự động fallback sang bộ nhớ In-Memory khi không có Redis.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        # Trạng thái dự phòng In-Memory
        self._local_state = CircuitState.CLOSED
        self._local_failures = 0
        self._local_last_change = time.time()

    async def get_state(self, redis: aioredis.Redis | None = None) -> CircuitState:
        """Lấy trạng thái hiện tại của Circuit Breaker, tự động chuyển sang HALF_OPEN nếu hết thời gian chờ."""
        now = time.time()

        if redis is not None:
            try:
                state_str = await redis.get(f"circuit:{self.name}:state")
                last_change_str = await redis.get(f"circuit:{self.name}:last_change")

                current_state = CircuitState(state_str.decode() if isinstance(state_str, bytes) else state_str) if state_str else CircuitState.CLOSED
                last_change = float(last_change_str) if last_change_str else now

                if current_state == CircuitState.OPEN:
                    if (now - last_change) >= self.recovery_timeout:
                        # Hết thời gian ngắt -> Chuyển sang HALF_OPEN thử nghiệm
                        await redis.set(f"circuit:{self.name}:state", CircuitState.HALF_OPEN.value)
                        await redis.set(f"circuit:{self.name}:last_change", str(now))
                        logger.info(f"Circuit Breaker [{self.name}]: OPEN -> HALF_OPEN (Thử nghiệm canary request)")
                        return CircuitState.HALF_OPEN

                return current_state
            except aioredis.RedisError:
                pass

        # Fallback In-Memory
        if self._local_state == CircuitState.OPEN:
            if (now - self._local_last_change) >= self.recovery_timeout:
                self._local_state = CircuitState.HALF_OPEN
                self._local_last_change = now
                logger.info(f"Circuit Breaker [{self.name}]: OPEN -> HALF_OPEN (Local In-Memory)")
        return self._local_state

    async def record_success(self, redis: aioredis.Redis | None = None) -> None:
        """Ghi nhận một lệnh gọi thành công -> Nếu đang HALF_OPEN thì đóng mạch lại CLOSED."""
        now = time.time()
        current_state = await self.get_state(redis)

        if current_state == CircuitState.HALF_OPEN:
            logger.info(f"Circuit Breaker [{self.name}]: Canary thành công! HALF_OPEN -> CLOSED")

        if redis is not None:
            try:
                if hasattr(redis, "pipeline"):
                    pipe = redis.pipeline()
                    pipe.set(f"circuit:{self.name}:state", CircuitState.CLOSED.value)
                    pipe.set(f"circuit:{self.name}:failures", "0")
                    pipe.set(f"circuit:{self.name}:last_change", str(now))
                    await pipe.execute()
                else:
                    await redis.set(f"circuit:{self.name}:state", CircuitState.CLOSED.value)
                    await redis.set(f"circuit:{self.name}:failures", "0")
                    await redis.set(f"circuit:{self.name}:last_change", str(now))
                return
            except Exception:
                pass

        self._local_state = CircuitState.CLOSED
        self._local_failures = 0
        self._local_last_change = now

    async def record_failure(self, redis: aioredis.Redis | None = None) -> None:
        """Ghi nhận một lệnh gọi thất bại -> Tăng bộ đếm lỗi hoặc lập tức ngắt mạch nếu đang HALF_OPEN."""
        now = time.time()
        current_state = await self.get_state(redis)

        if current_state == CircuitState.HALF_OPEN:
            # Canary thất bại -> Lập tức quay lại OPEN
            logger.warning(f"Circuit Breaker [{self.name}]: Canary thất bại! HALF_OPEN -> OPEN")
            await self._set_state(CircuitState.OPEN, now, redis)
            return

        # Đang CLOSED -> Tăng failure count
        if redis is not None:
            try:
                failures = await redis.incr(f"circuit:{self.name}:failures")
                if failures >= self.failure_threshold:
                    logger.warning(f"Circuit Breaker [{self.name}]: Đạt ngưỡng lỗi ({failures}/{self.failure_threshold}) -> Ngắt mạch OPEN")
                    await self._set_state(CircuitState.OPEN, now, redis)
                return
            except Exception:
                pass

        self._local_failures += 1
        if self._local_failures >= self.failure_threshold:
            logger.warning(f"Circuit Breaker [{self.name}]: Đạt ngưỡng lỗi In-Memory ({self._local_failures}/{self.failure_threshold}) -> OPEN")
            self._local_state = CircuitState.OPEN
            self._local_last_change = now

    async def _set_state(self, new_state: CircuitState, timestamp: float, redis: aioredis.Redis | None = None) -> None:
        if redis is not None:
            try:
                if hasattr(redis, "pipeline"):
                    pipe = redis.pipeline()
                    pipe.set(f"circuit:{self.name}:state", new_state.value)
                    pipe.set(f"circuit:{self.name}:last_change", str(timestamp))
                    await pipe.execute()
                else:
                    await redis.set(f"circuit:{self.name}:state", new_state.value)
                    await redis.set(f"circuit:{self.name}:last_change", str(timestamp))
                return
            except Exception:
                pass

        self._local_state = new_state
        self._local_last_change = timestamp

    async def reset(self, redis: aioredis.Redis | None = None) -> None:
        """Admin chủ động reset trạng thái mạch về CLOSED và xóa bộ đếm lỗi."""
        now = time.time()
        if redis is not None:
            try:
                if hasattr(redis, "pipeline"):
                    pipe = redis.pipeline()
                    pipe.set(f"circuit:{self.name}:state", CircuitState.CLOSED.value)
                    pipe.set(f"circuit:{self.name}:failures", "0")
                    pipe.set(f"circuit:{self.name}:last_change", str(now))
                    await pipe.execute()
                else:
                    await redis.set(f"circuit:{self.name}:state", CircuitState.CLOSED.value)
                    await redis.set(f"circuit:{self.name}:failures", "0")
                    await redis.set(f"circuit:{self.name}:last_change", str(now))
                return
            except Exception:
                pass

        self._local_state = CircuitState.CLOSED
        self._local_failures = 0
        self._local_last_change = now

    async def get_snapshot(self, redis: aioredis.Redis | None = None) -> dict[str, Any]:
        """Lấy toàn bộ thông số giám sát hiện tại của Circuit Breaker."""
        state = await self.get_state(redis)
        failures = self._local_failures
        last_change = self._local_last_change

        if redis is not None:
            try:
                f_str = await redis.get(f"circuit:{self.name}:failures")
                lc_str = await redis.get(f"circuit:{self.name}:last_change")
                if f_str:
                    failures = int(f_str)
                if lc_str:
                    last_change = float(lc_str)
            except aioredis.RedisError:
                pass

        time_in_state = round(time.time() - last_change, 1)
        return {
            "name": self.name,
            "state": state.value,
            "failure_count": failures,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_seconds": self.recovery_timeout,
            "seconds_in_current_state": time_in_state,
        }

    async def execute(
        self,
        func: Callable[..., Any],
        *args: Any,
        fallback: Callable[..., Any] | None = None,
        redis: aioredis.Redis | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Thực thi hàm đích dưới sự bảo vệ của Circuit Breaker:
        - Nếu mạch OPEN: Lập tức từ chối hoặc gọi fallback (Fail Fast).
        - Nếu gọi lỗi: Ghi nhận failure count và chuyển mạch nếu vượt ngưỡng.
        """
        state = await self.get_state(redis)
        if state == CircuitState.OPEN:
            if fallback is not None:
                logger.info(f"Circuit Breaker [{self.name}]: Đang OPEN -> Thực thi fallback")
                if inspect.iscoroutinefunction(fallback):
                    return await fallback(*args, **kwargs)
                return fallback(*args, **kwargs)

            retry_after = max(1, int(self.recovery_timeout - (time.time() - self._local_last_change)))
            raise CircuitBreakerOpenException(circuit_name=self.name, retry_after=retry_after)

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            await self.record_success(redis)
            return result
        except Exception as exc:
            await self.record_failure(redis)
            if fallback is not None:
                logger.warning(f"Circuit Breaker [{self.name}]: Lỗi '{exc}', kích hoạt fallback")
                if inspect.iscoroutinefunction(fallback):
                    return await fallback(*args, **kwargs)
                return fallback(*args, **kwargs)
            raise exc


class CircuitBreakerRegistry:
    """Quản lý tập trung các Circuit Breaker trong toàn bộ ứng dụng."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        # Đăng ký sẵn các Circuit Breaker mặc định
        self.register(CircuitBreaker(name="payment_gateway", failure_threshold=3, recovery_timeout=10.0))
        self.register(CircuitBreaker(name="sms_notification", failure_threshold=3, recovery_timeout=15.0))
        self.register(CircuitBreaker(name="external_maps", failure_threshold=5, recovery_timeout=20.0))

    def register(self, breaker: CircuitBreaker) -> None:
        self._breakers[breaker.name] = breaker

    def get(self, name: str) -> CircuitBreaker | None:
        return self._breakers.get(name)

    def get_all(self) -> list[CircuitBreaker]:
        return list(self._breakers.values())


circuit_registry = CircuitBreakerRegistry()
