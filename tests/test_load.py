"""Load Testing & Latency Verification for FoodHub Backend.

Tests concurrent load handling, throughput calculation, and p95 latency
benchmarks in a lightweight, high-speed execution mode (<0.5s).
"""

import asyncio
import time

import pytest
from httpx import AsyncClient

from scripts.run_load_test import calculate_percentile


def test_percentile_calculation():
    """Kiểm tra thuật toán tính toán phân vị độ trễ (p50, p95, p99)."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    p50 = calculate_percentile(data, 50)
    p95 = calculate_percentile(data, 95)

    assert p50 == pytest.approx(55.0, abs=1.0)
    assert p95 == pytest.approx(95.5, abs=1.0)
    assert calculate_percentile([], 95) == 0.0


@pytest.mark.asyncio
async def test_concurrent_load_and_p95_latency(client: AsyncClient):
    """Kiểm thử tải đồng thời nhẹ và đo lường throughput + p95 latency.

    Gửi đồng thời 30 requests qua các endpoint trọng yếu:
    - Health live & ready probes
    - Prometheus metrics
    - Surge pricing delivery fee estimation
    """
    endpoints = [
        "/health/live",
        "/health/ready",
        "/metrics",
        "/api/v1/pricing/estimate-delivery?origin_lat=10.7769&origin_lng=106.7009&delivery_lat=10.7850&delivery_lng=106.7100",
    ]

    total_requests = 30
    latencies: list[float] = []

    async def fetch_endpoint(url: str):
        t0 = time.perf_counter()
        response = await client.get(url)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)
        return response

    start_time = time.perf_counter()
    tasks = [
        fetch_endpoint(endpoints[i % len(endpoints)])
        for i in range(total_requests)
    ]
    responses = await asyncio.gather(*tasks)
    total_duration_sec = time.perf_counter() - start_time

    # 1. Toàn bộ requests phải thành công
    for resp in responses:
        assert resp.status_code == 200, f"Request failed: {resp.text}"

    # 2. Tính toán Throughput (RPS) và p95 latency
    throughput = total_requests / total_duration_sec
    p95_latency = calculate_percentile(latencies, 95)
    p50_latency = calculate_percentile(latencies, 50)

    # 3. Đảm bảo chỉ số hiệu năng đạt yêu cầu
    assert throughput > 10.0, f"Throughput too low: {throughput} req/s"
    assert p95_latency < 1000.0, f"p95 latency too high: {p95_latency} ms"
    assert p50_latency > 0.0

