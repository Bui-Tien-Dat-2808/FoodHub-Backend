"""FoodHub Quick Load Test & Latency Benchmark Runner.

Executes a lightweight, high-performance load test measuring throughput (RPS)
and p95 latency for FoodHub API.

Supports two modes:
1. Direct ASGI mode (in-process, super fast, no separate server needed):
   python scripts/run_load_test.py --mode direct --concurrency 20 --requests 100

2. Locust headless mode (against live HTTP server):
   python scripts/run_load_test.py --mode locust --users 10 --duration 5 --host http://127.0.0.1:8000
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def calculate_percentile(data: list[float], percentile: float) -> float:
    """Calculate percentile from a sorted list of floats."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (percentile / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * d


async def run_direct_asgi_benchmark(total_requests: int = 100, concurrency: int = 20) -> None:
    """Runs concurrent async requests directly against the FastAPI app via ASGITransport."""
    from httpx import ASGITransport, AsyncClient

    from app.core.redis import get_redis
    from app.main import app

    # Check dependencies availability and provide lightweight fallback if offline
    try:
        import redis.asyncio as aioredis

        from app.core.config import settings
        test_r = aioredis.from_url(settings.redis_url, socket_connect_timeout=0.5)
        await asyncio.wait_for(test_r.ping(), timeout=0.5)
        await test_r.aclose()
    except Exception:
        class FakeRedisBench:
            async def get(self, *a, **kw): return None
            async def set(self, *a, **kw): return True
            async def delete(self, *a, **kw): return 1
            async def ping(self): return True
            async def aclose(self): pass

        fake_r = FakeRedisBench()
        async def override_redis():
            yield fake_r
        app.dependency_overrides[get_redis] = override_redis

    endpoints = [
        ("GET", "/health/live", None),
        ("GET", "/health/ready", None),
        ("GET", "/metrics", None),
        (
            "GET",
            "/api/v1/pricing/estimate-delivery?origin_lat=10.7769&origin_lng=106.7009&delivery_lat=10.7850&delivery_lng=106.7100",
            None,
        ),
    ]

    print("=" * 65)
    print(" FOODHUB FAST IN-PROCESS LOAD BENCHMARK")
    print(f" Requests: {total_requests} | Concurrency: {concurrency}")
    print("=" * 65)

    transport = ASGITransport(app=app)
    latencies: list[float] = []
    success_count = 0
    failure_count = 0
    status_codes: dict[int, int] = {}

    semaphore = asyncio.Semaphore(concurrency)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def send_single_request(idx: int) -> None:
            nonlocal success_count, failure_count
            method, path, body = endpoints[idx % len(endpoints)]
            async with semaphore:
                t0 = time.perf_counter()
                try:
                    if method == "GET":
                        resp = await client.get(path)
                    else:
                        resp = await client.post(path, json=body)
                    latency_ms = (time.perf_counter() - t0) * 1000
                    latencies.append(latency_ms)
                    code = resp.status_code
                    status_codes[code] = status_codes.get(code, 0) + 1
                    if 200 <= code < 400:
                        success_count += 1
                    else:
                        failure_count += 1
                except Exception:
                    failure_count += 1

        start_time = time.perf_counter()
        tasks = [send_single_request(i) for i in range(total_requests)]
        await asyncio.gather(*tasks)
        total_duration_sec = time.perf_counter() - start_time

    throughput = round(total_requests / total_duration_sec, 2) if total_duration_sec > 0 else 0
    p50 = round(calculate_percentile(latencies, 50), 2)
    p90 = round(calculate_percentile(latencies, 90), 2)
    p95 = round(calculate_percentile(latencies, 95), 2)
    p99 = round(calculate_percentile(latencies, 99), 2)
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0

    print(f"\n Finished {total_requests} requests in {round(total_duration_sec, 3)} seconds.")
    print("-" * 65)
    print(f" Throughput (RPS):      {throughput} req/s")
    print(f" Success / Failure:     {success_count} / {failure_count}")
    print(f" Status Codes:          {status_codes}")
    print(f" Latency Avg:           {avg_latency} ms")
    print(f" Latency Median (p50):  {p50} ms")
    print(f" Latency p90:           {p90} ms")
    print(f" Latency p95:           {p95} ms")
    print(f" Latency p99:           {p99} ms")
    print("=" * 65)


def run_locust_headless(users: int, duration_sec: int, host: str) -> None:
    """Executes Locust in headless mode."""
    locust_file = os.path.join(os.path.dirname(__file__), "..", "load_tests", "locustfile.py")
    spawn_rate = max(1, users // 2)
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        locust_file,
        "--headless",
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "--run-time",
        f"{duration_sec}s",
        "--host",
        host,
    ]
    print(f"Executing: {' '.join(cmd)}")
    subprocess.run(cmd, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="FoodHub Load Test Runner")
    parser.add_argument(
        "--mode",
        choices=["direct", "locust"],
        default="direct",
        help="Run mode: direct (fast in-process ASGI) or locust (headless HTTP)",
    )
    parser.add_argument("--requests", type=int, default=100, help="Total requests for direct mode")
    parser.add_argument("--concurrency", type=int, default=20, help="Concurrency level")
    parser.add_argument("--users", type=int, default=10, help="Locust virtual users")
    parser.add_argument("--duration", type=int, default=5, help="Locust test duration in seconds")
    parser.add_argument("--host", default="http://127.0.0.1:8000", help="Target API host")

    args = parser.parse_args()

    if args.mode == "direct":
        asyncio.run(
            run_direct_asgi_benchmark(
                total_requests=args.requests,
                concurrency=args.concurrency,
            )
        )
    else:
        run_locust_headless(users=args.users, duration_sec=args.duration, host=args.host)


if __name__ == "__main__":
    main()

