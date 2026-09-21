import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

# 1. tạo instance Celery
celery_app = Celery(
    "foodhub_worker",
    broker=settings.redis_url,
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
    include=[
        "app.tasks.notification_tasks",
        "app.tasks.order_tasks",
        "app.tasks.payment_tasks",
        "app.tasks.reconciliation_tasks",
    ],
)

# 2. Cấu hình Celery
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    task_track_started=True,
    # Task chạy lúc 23:59 hàng ngày
    beat_schedule={
        "daily-reconciliation-midnight": {
            "task": "app.tasks.reconciliation_tasks.daily_reconciliation_task",
            "schedule": crontab(hour=23, minute=59),
        },
    },
)


# 3. Helper cầu nối thực thi Coroutine trong worker
def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Chạy 1 coroutine async trong Celery task sync."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
