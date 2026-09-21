import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def async_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()

@pytest.fixture
async def client(async_db):
    async def override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest.mark.anyio
async def test_register_and_login(client: AsyncClient):
    # 1. Đăng ký tài khoản
    reg_res = await client.post("/api/v1/auth/register", json= {
        "email": "customer@foodhub.com",
        "password": "securepassword123",
        "full_name": "Nguyen Van A",
        "role": "CUSTOMER"
    })
    assert reg_res.status_code == 201
    data = reg_res.json()
    assert data["email"] == "customer@foodhub.com"
    assert "hashed_password" not in data

    # 2. Đăng nhập lấy token
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "customer@foodhub.com",
        "password": "securepassword123"
    })
    assert login_res.status_code == 200
    tokens = login_res.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

@pytest.mark.anyio
async def test_rbac_customer_cannot_create_restaurant(client: AsyncClient):
    # Đăng ký Customer
    await client.post("/api/v1/auth/register", json={
        "email": "user1@foodhub.com",
        "password": "password123",
        "full_name": "Customer User",
        "role": "CUSTOMER"
    })
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "user1@foodhub.com",
        "password": "password123"
    })
    token = login_res.json()["access_token"]
    
    # Customer cố tình tạo nhà hàng -> Mong đợi HTTP 403 Forbidden
    res = await client.post(
        "/api/v1/restaurants/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Pho Bo Ha Noi",
            "address": "123 Nguyen Hue, Q1",
            "latitude": 10.7769,
            "longitude": 106.7009
        }
    )
    assert res.status_code == 403