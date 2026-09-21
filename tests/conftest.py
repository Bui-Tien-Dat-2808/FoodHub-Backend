import time
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models
from app.core.database import Base, get_db
from app.core.redis import get_redis
from app.main import app

TEST_DB_FILE = "./test.db"
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DB_FILE}"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Khởi tạo database sạch cho mỗi test case."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

class FakeRedis:
    """Giả lập Redis cho môi trường test"""
    def __init__(self):
        self.data = {}
        self.expiry = {}

    async def get(self, key: str):
        if key in self.expiry and time.time() > self.expiry[key]:
            self.data.pop(key, None)
            self.expiry.pop(key, None)
            return None
        return self.data.get(key)

    async def set(self, key: str, value, ex: int | None = None):
        self.data[key] = str(value)
        if ex:
            self.expiry[key] = time.time() + ex

    async def delete(self, key: str):
        self.data.pop(key, None)
        self.expiry.pop(key, None)

    async def incr(self, key: str):
        val = int(self.data.get(key, 0)) + 1
        self.data[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int):
        if key in self.data:
            self.expiry[key] = time.time() + seconds

    async def ttl(self, key: str):
        if key in self.expiry:
            remaining = int(self.expiry[key] - time.time())
            return max(1, remaining)
        return -1

    async def publish(self, channel: str, message: str):
        return 1

    async def aclose(self):
        pass


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Test client với database và redis override độc lập mỗi request."""
    fake_redis_instance = FakeRedis()

    async def override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.rollback()
                await session.close()

    async def override_get_redis():
        yield fake_redis_instance

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_celery_tasks(monkeypatch):
    from unittest.mock import MagicMock

    from app.tasks.notification_tasks import send_order_status_notification
    from app.tasks.order_tasks import auto_cancel_unpaid_order

    mock_send = MagicMock()
    mock_cancel = MagicMock()
    monkeypatch.setattr(send_order_status_notification, "delay", mock_send)
    monkeypatch.setattr(auto_cancel_unpaid_order, "apply_async", mock_cancel)
    return {"send_notification": mock_send, "auto_cancel": mock_cancel}


@pytest.fixture(autouse=True)
def override_celery_db(monkeypatch):
    import app.tasks.order_tasks
    import app.tasks.reconciliation_tasks

    monkeypatch.setattr(app.tasks.order_tasks, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(app.tasks.reconciliation_tasks, "AsyncSessionLocal", TestSessionLocal)




