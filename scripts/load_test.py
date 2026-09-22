"""Concurrency Load Test Script for FoodHub Backend.

Demonstrates and verifies ZERO OVERSELL under high concurrent load:
- Simulates 25 distinct customers concurrently attempting to order a flash-sale item with only 5 units in stock.
- Confirms exactly 5 succeed (201 Created) and 20 fail (400 Bad Request: Out of stock).
- Verifies final stock is exactly 0 and never drops below zero.

Usage:
    python scripts/load_test.py [API_BASE_URL]
    Default URL: http://127.0.0.1:8000
"""

import asyncio
import os
import sys
import time
from uuid import uuid4

import httpx

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.enums import UserRole
from app.models.user import User

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


async def get_customer_auth_tokens(count: int = 25) -> list[str]:
    """Lấy danh sách JWT access token của 25 khách hàng khác nhau để test tải chân thực."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(User)
            .where(User.role == UserRole.CUSTOMER, User.is_active.is_(True))
            .limit(count)
        )
        users = (await db.execute(stmt)).scalars().all()
        tokens = [
            create_access_token({"sub": str(u.id), "role": u.role.value})
            for u in users
        ]
        return tokens


async def run_load_test(base_url: str = "http://127.0.0.1:8000") -> bool:
    print("=" * 65)
    print("[START] KIEM THU TAI DONG THOI (CONCURRENCY LOAD TEST)")
    print(f"Target API: {base_url}")
    print("=" * 65)

    # 1. Lấy danh sách token của 25 khách hàng
    tokens = await get_customer_auth_tokens(count=25)
    if len(tokens) < 10:
        print("[ERROR] So luong khach hang trong DB qua it. Vui long chay scripts/seed.py truoc.")
        return False

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # 2. Kiểm tra API Health
        try:
            health = await client.get("/health")
            if health.status_code != 200:
                print(f"[ERROR] API server khong phan hoi hop le tai {base_url}")
                return False
        except Exception as exc:
            print(f"[ERROR] Khong the ket noi toi {base_url}: {exc}")
            print("Vui long dam bao FastAPI server dang chay: uvicorn app.main:app --reload")
            return False

        # 3. Lấy thông tin nhà hàng và món flash sale
        rests_res = await client.get("/api/v1/restaurants/")
        restaurants = rests_res.json()
        target_rest = next(
            (r for r in restaurants if "Cơm Tấm" in r["name"]),
            restaurants[0] if restaurants else None,
        )

        if not target_rest:
            print("[ERROR] Khong tim thay nha hang. Vui long chay scripts/seed.py.")
            return False

        rest_id = target_rest["id"]
        menu_res = await client.get(f"/api/v1/menu/restaurants/{rest_id}/items")
        items = menu_res.json()

        flash_item = next(
            (i for i in items if "Flash Sale" in i["name"] or "Hoàng Gia" in i["name"]),
            None,
        )

        if not flash_item:
            flash_item = items[0]

        item_id = flash_item["id"]
        initial_stock = flash_item["stock_quantity"]
        print(f"[*] Nha hang:              {target_rest['name']} (ID: {rest_id})")
        print(f"[*] Mon kiem thu:          {flash_item['name']} (ID: {item_id})")
        print(f"[*] So luong ton kho:      {initial_stock} suat")

        if initial_stock <= 0:
            print("[WARN] Mon da het hang (stock = 0). Vui long chay lai scripts/seed.py de reset ton kho.")
            return False

        concurrent_count = min(len(tokens), max(15, initial_stock * 4))
        print(f"\n[*] Ban {concurrent_count} request tu {concurrent_count} khach hang DONG THOI...")

        # 4. Hàm gửi request tạo đơn hàng của từng khách
        async def place_order(idx: int, token: str) -> dict:
            idem_key = f"loadtest-{uuid4()}"
            payload = {
                "restaurant_id": rest_id,
                "items": [{"menu_item_id": item_id, "quantity": 1}],
                "delivery_lat": 10.7890,
                "delivery_lng": 106.6950,
                "delivery_address": f"Dia chi giao hang {idx}",
            }
            req_headers = {
                "Authorization": f"Bearer {token}",
                "X-Idempotency-Key": idem_key,
            }
            try:
                t_start = time.perf_counter()
                res = await client.post("/api/v1/orders/", json=payload, headers=req_headers)
                latency = round((time.perf_counter() - t_start) * 1000, 2)
                return {
                    "idx": idx,
                    "status_code": res.status_code,
                    "latency_ms": latency,
                    "data": res.json() if res.headers.get("content-type", "").startswith("application/json") else res.text,
                }
            except Exception as e:
                return {"idx": idx, "status_code": 500, "error": str(e)}

        # 5. Kích hoạt toàn bộ requests đồng thời qua asyncio.gather
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[place_order(i + 1, tokens[i]) for i in range(concurrent_count)]
        )
        total_time_ms = round((time.perf_counter() - t0) * 1000, 2)

        # 6. Thống kê kết quả
        success_count = sum(1 for r in results if r["status_code"] == 201)
        out_of_stock_count = sum(1 for r in results if r["status_code"] == 400)
        rate_limited_count = sum(1 for r in results if r["status_code"] == 429)
        other_errors = [r for r in results if r["status_code"] not in (201, 400, 429)]

        # 7. Kiểm tra tồn kho sau khi bắn tải
        after_item_res = await client.get(f"/api/v1/menu/items/{item_id}")
        final_stock = after_item_res.json()["stock_quantity"]

        print("\n" + "=" * 65)
        print("CONCURRENCY TEST REPORT (BAO CAO KIEM THU DONG THOI)")
        print("=" * 65)
        print(f" - Tong so request gui di:       {concurrent_count}")
        print(f" - Tong thoi gian xu ly:         {total_time_ms} ms")
        print(f" - Don dat thanh cong (201):     {success_count} don")
        print(f" - Don bi tu choi het hang (400): {out_of_stock_count} don")
        if rate_limited_count > 0:
            print(f" - Bi chan Rate Limit (429):     {rate_limited_count} don")
        if other_errors:
            print(f" - Loi khac ({len(other_errors)}):              {[e['status_code'] for e in other_errors]}")
        print(f" - Ton kho ban dau:              {initial_stock} suat")
        print(f" - Ton kho thuc te con lai:      {final_stock} suat")
        print("-" * 65)

        # 8. Phán quyết Zero-Oversell
        is_passed = (success_count == initial_stock) and (final_stock == 0)
        if is_passed:
            print(">> KET LUAN: [PASSED] ZERO OVERSELL!")
            print(f"   He thong ban dung chinh xac {success_count}/{initial_stock} suat.")
            print("   Khong bi am kho hay ban thua du nhan tai dong thoi cao.")
        else:
            print(">> KET LUAN: [FAILED] CO SAI LECH SO LUONG!")
            print(f"   Ky vong thanh cong: {initial_stock}, Thuc te: {success_count}. Ton kho: {final_stock}")
        print("=" * 65)
        return is_passed


if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    asyncio.run(run_load_test(target_url))

