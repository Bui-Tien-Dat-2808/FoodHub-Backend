"""Locust Load Testing Scenario for FoodHub Backend.

Benchmarks throughput (RPS) and p95 latency under concurrent load:
- Browsing restaurants list
- Viewing restaurant menu items (cached via Redis)
- Calculating dynamic delivery fee & surge pricing
- K8s Health & Readiness probes
- Prometheus metrics scraping

Usage:
    # Run interactive Web UI at http://localhost:8089:
    locust -f load_tests/locustfile.py --host http://localhost:8000

    # Run fast headless benchmark (5s, 10 users):
    locust -f load_tests/locustfile.py --headless -u 10 -r 5 --run-time 5s --host http://localhost:8000
"""

import logging

from locust import HttpUser, between, events, task

logger = logging.getLogger("locust.foodhub")


class FoodHubUser(HttpUser):
    """Simulates realistic customer and monitoring traffic on FoodHub."""

    wait_time = between(0.05, 0.2)

    @task(4)
    def browse_restaurants(self) -> None:
        """Khách hàng duyệt danh sách nhà hàng."""
        self.client.get("/api/v1/restaurants/", name="/api/v1/restaurants/")

    @task(3)
    def view_restaurant_menu(self) -> None:
        """Khách hàng xem thực đơn nhà hàng (tận dụng Redis cache-aside)."""
        self.client.get(
            "/api/v1/menu/restaurants/1/items",
            name="/api/v1/menu/restaurants/{id}/items",
        )

    @task(3)
    def estimate_delivery_fee(self) -> None:
        """Khách hàng xem trước phí giao hàng và hệ số surge pricing."""
        params = {
            "origin_lat": 10.7769,
            "origin_lng": 106.7009,
            "delivery_lat": 10.7850,
            "delivery_lng": 106.7100,
        }
        self.client.get(
            "/api/v1/pricing/estimate-delivery",
            params=params,
            name="/api/v1/pricing/estimate-delivery",
        )

    @task(2)
    def health_probes(self) -> None:
        """K8s liveness & readiness probes."""
        self.client.get("/health/live", name="/health/live")
        self.client.get("/health/ready", name="/health/ready")

    @task(1)
    def scrape_metrics(self) -> None:
        """Prometheus scraper thu thập metrics hệ thống."""
        self.client.get("/metrics", name="/metrics")


@events.quitting.add_listener
def on_quitting(environment, **kwargs) -> None:
    """In bảng tổng kết throughput và p95 latency sau khi kết thúc load test."""
    stats = environment.runner.stats.total
    if stats.num_requests > 0:
        total_reqs = stats.num_requests
        fails = stats.num_failures
        rps = round(stats.total_rps, 2)
        p50 = round(stats.get_response_time_percentile(0.50), 2)
        p95 = round(stats.get_response_time_percentile(0.95), 2)
        p99 = round(stats.get_response_time_percentile(0.99), 2)
        avg = round(stats.avg_response_time, 2)

        print("\n" + "=" * 65)
        print("         FOODHUB LOAD TEST BENCHMARK SUMMARY (LOCUST)")
        print("=" * 65)
        print(f" Total Requests:    {total_reqs}")
        print(f" Failed Requests:   {fails} ({round(fails / total_reqs * 100, 2)}%)")
        print(f" Throughput (RPS):  {rps} req/s")
        print(f" Avg Latency:       {avg} ms")
        print(f" Median (p50):      {p50} ms")
        print(f" 95th pct (p95):    {p95} ms")
        print(f" 99th pct (p99):    {p99} ms")
        print("=" * 65 + "\n")

