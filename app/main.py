from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models
from app.api.v1.endpoints import health, websockets
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import Base, engine
from app.core.metrics import PrometheusMetricsMiddleware, get_metrics_response
from app.middleware.security import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tạo các bảng trong cơ sở dữ liệu nếu chưa tồn tại
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PrometheusMetricsMiddleware)

app.include_router(api_router, prefix="/api/v1")
app.include_router(websockets.router, prefix="/ws", tags=["WebSocket"])
app.include_router(health.router, prefix="/health", tags=["Health & Probes"])


@app.get("/metrics", tags=["Observability"])
async def prometheus_metrics():
    """Endpoint xuất số liệu metrics chuẩn Prometheus cho hệ thống giám sát (Prometheus/Grafana)."""
    return get_metrics_response()


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy", "service": settings.PROJECT_NAME, "version": settings.VERSION}