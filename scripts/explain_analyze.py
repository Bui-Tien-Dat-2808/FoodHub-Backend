import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import text

from app.core.database import engine


async def run_explain():
    async with engine.connect() as conn:
        print("=== 1. Khi bật Sequential Scan (hoặc bảng ít dữ liệu) ===")
        res1 = await conn.execute(
            text("""
            EXPLAIN (ANALYZE, BUFFERS)
            SELECT id, order_code, status, total_amount, created_at
            FROM orders
            WHERE status = 'DELIVERED'
              AND created_at >= NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC;
        """)
        )
        for row in res1.fetchall():
            print(row[0])

        print("\n=== 2. Ép sử dụng Composite Index (SET enable_seqscan = OFF) ===")
        await conn.execute(text("SET enable_seqscan = OFF;"))
        res2 = await conn.execute(
            text("""
            EXPLAIN (ANALYZE, BUFFERS)
            SELECT id, order_code, status, total_amount, created_at
            FROM orders
            WHERE status = 'DELIVERED'
              AND created_at >= NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC;
        """)
        )
        for row in res2.fetchall():
            print(row[0])


if __name__ == "__main__":
    asyncio.run(run_explain())
