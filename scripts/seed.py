"""Seed data script for FoodHub Backend.

Generates:
- 1 Admin
- 2 Merchants (with 2 Restaurants & 10 MenuItems)
- 3 Customers
- 2 Drivers (with DriverProfiles)
- 3 Vouchers (Percentage, Fixed, Limited for concurrency test)

Usage:
    python scripts/seed.py
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import get_password_hash
from app.models.driver import DriverProfile
from app.models.enums import DiscountType, UserRole
from app.models.promotion import Voucher
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User


async def seed() -> None:
    print("=" * 60)
    print("[START] Bat dau khoi tao du lieu mau (Seeding Database)...")
    print("=" * 60)

    # 1. Tạo bảng nếu chưa tồn tại và cập nhật schema tương thích
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS rating FLOAT DEFAULT 5.0;")
        )
        await conn.execute(
            text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;")
        )

    async with AsyncSessionLocal() as db:
        # 2. Tạo Admin
        admin_email = "admin@foodhub.com"
        admin = (
            await db.execute(select(User).where(User.email == admin_email))
        ).scalar_one_or_none()
        if not admin:
            admin = User(
                email=admin_email,
                hashed_password=get_password_hash("admin123"),
                full_name="Quản Trị Viên Hệ Thống",
                phone="0901234567",
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(admin)
            print("  [+] Tạo Admin:", admin_email)

        # 3. Tạo Merchants
        merchants_data = [
            {
                "email": "merchant_pho@foodhub.com",
                "password": "merchant123",
                "name": "Chủ Quán Phở Bát Đàn",
                "phone": "0912345678",
                "restaurant": {
                    "name": "Phở Gia Truyền Bát Đàn",
                    "address": "49 Bát Đàn, Phường Cửa Đông, Quận Hoàn Kiếm",
                    "latitude": 21.0335,
                    "longitude": 105.8475,
                    "items": [
                        {"name": "Phở Bò Tái Nạm", "price": 55000, "stock": 50},
                        {"name": "Phở Bò Gầu Giòn", "price": 60000, "stock": 30},
                        {"name": "Quẩy Giòn Hà Nội", "price": 10000, "stock": 100},
                        {"name": "Trứng Gà Chần", "price": 10000, "stock": 50},
                        {"name": "Trà Đá Hoa Nhài", "price": 5000, "stock": 200},
                    ],
                },
            },
            {
                "email": "merchant_comtam@foodhub.com",
                "password": "merchant123",
                "name": "Chủ Quán Cơm Tấm Cali",
                "phone": "0923456789",
                "restaurant": {
                    "name": "Cơm Tấm Sài Gòn Xưa",
                    "address": "128 Đinh Tiên Hoàng, Quận 1",
                    "latitude": 10.7890,
                    "longitude": 106.6950,
                    "items": [
                        {"name": "Cơm Tấm Sườn Bì Chả", "price": 65000, "stock": 40},
                        {"name": "Cơm Tấm Ba Rọi Nướng", "price": 50000, "stock": 50},
                        {"name": "Canh Khổ Qua Nhồi Thịt", "price": 20000, "stock": 30},
                        {"name": "Chả Trứng Hấp Đặc Biệt", "price": 15000, "stock": 50},
                        {
                            "name": "Cơm Tấm Sườn Cây Hoàng Gia (Flash Sale)",
                            "price": 99000,
                            "stock": 5,
                        },  # Dành cho concurrency test
                    ],
                },
            },
        ]

        for m_data in merchants_data:
            m_user = (
                await db.execute(select(User).where(User.email == m_data["email"]))
            ).scalar_one_or_none()
            if not m_user:
                m_user = User(
                    email=m_data["email"],
                    hashed_password=get_password_hash(m_data["password"]),
                    full_name=m_data["name"],
                    phone=m_data["phone"],
                    role=UserRole.MERCHANT,
                    is_active=True,
                )
                db.add(m_user)
                await db.flush()
                print(f"  [+] Tạo Merchant: {m_data['email']}")

            r_info = m_data["restaurant"]
            restaurant = (
                await db.execute(
                    select(Restaurant).where(Restaurant.name == r_info["name"])
                )
            ).scalar_one_or_none()
            if not restaurant:
                restaurant = Restaurant(
                    owner_id=m_user.id,
                    name=r_info["name"],
                    address=r_info["address"],
                    latitude=r_info["latitude"],
                    longitude=r_info["longitude"],
                    is_open=True,
                )
                db.add(restaurant)
                await db.flush()
                print(f"    [+] Tạo Nhà hàng: {r_info['name']}")

                for item in r_info["items"]:
                    menu_item = MenuItem(
                        restaurant_id=restaurant.id,
                        name=item["name"],
                        base_price=item["price"],
                        stock_quantity=item["stock"],
                        is_available=True,
                    )
                    db.add(menu_item)
                    print(
                        f"      [-] Thêm món: {item['name']} ({item['price']:,}đ, Tồn: {item['stock']})"
                    )

        # 4. Tạo Customers (25 khách hàng phục vụ bài test tải đồng thời)
        customers_data = [
            {
                "email": f"customer{i}@foodhub.com",
                "name": f"Khách Hàng {i}",
                "phone": f"09345678{i:02d}",
            }
            for i in range(1, 26)
        ]
        created_c_count = 0
        for c in customers_data:
            c_user = (
                await db.execute(select(User).where(User.email == c["email"]))
            ).scalar_one_or_none()
            if not c_user:
                c_user = User(
                    email=c["email"],
                    hashed_password=get_password_hash("customer123"),
                    full_name=c["name"],
                    phone=c["phone"],
                    role=UserRole.CUSTOMER,
                    is_active=True,
                )
                db.add(c_user)
                created_c_count += 1
        if created_c_count > 0:
            print(f"  [+] Đã tạo {created_c_count} khách hàng mẫu (customer1@foodhub.com -> customer25@foodhub.com)")

        # 5. Tạo Drivers & DriverProfile
        drivers_data = [
            {
                "email": "driver1@foodhub.com",
                "name": "Vũ Anh Dũng (Shipper 1)",
                "phone": "0967890123",
                "plate": "29-E1 88888",
                "lat": 21.0330,
                "lng": 105.8470,
            },
            {
                "email": "driver2@foodhub.com",
                "name": "Đặng Quốc Hưng (Shipper 2)",
                "phone": "0978901234",
                "plate": "59-A1 99999",
                "lat": 10.7885,
                "lng": 106.6945,
            },
        ]
        for d in drivers_data:
            d_user = (
                await db.execute(select(User).where(User.email == d["email"]))
            ).scalar_one_or_none()
            if not d_user:
                d_user = User(
                    email=d["email"],
                    hashed_password=get_password_hash("driver123"),
                    full_name=d["name"],
                    phone=d["phone"],
                    role=UserRole.DRIVER,
                    is_active=True,
                )
                db.add(d_user)
                await db.flush()
                print(f"  [+] Tạo Tài xế: {d['email']}")

                profile = DriverProfile(
                    user_id=d_user.id,
                    license_plate=d["plate"],
                    current_lat=d["lat"],
                    current_lng=d["lng"],
                    is_online=True,
                    is_busy=False,
                    rating=5.0,
                )
                db.add(profile)
                print(f"    [+] Tạo Hồ sơ tài xế: Biển số {d['plate']}")

        # 6. Tạo Vouchers
        now = datetime.now(UTC)
        vouchers_data = [
            {
                "code": "FOODHUB50",
                "discount_type": DiscountType.PERCENTAGE,
                "discount_value": 50,
                "min_order_value": 50000,
                "max_discount": 40000,
                "usage_limit": 100,
                "valid_from": now - timedelta(days=1),
                "valid_to": now + timedelta(days=30),
            },
            {
                "code": "FREESHIP20K",
                "discount_type": DiscountType.FIXED,
                "discount_value": 20000,
                "min_order_value": 60000,
                "max_discount": 0,
                "usage_limit": 200,
                "valid_from": now - timedelta(days=1),
                "valid_to": now + timedelta(days=30),
            },
            {
                "code": "FLASH5",
                "discount_type": DiscountType.FIXED,
                "discount_value": 30000,
                "min_order_value": 80000,
                "max_discount": 0,
                "usage_limit": 5,  # Dành cho concurrency test
                "valid_from": now - timedelta(days=1),
                "valid_to": now + timedelta(days=30),
            },
        ]
        for v in vouchers_data:
            existing_v = (
                await db.execute(select(Voucher).where(Voucher.code == v["code"]))
            ).scalar_one_or_none()
            if not existing_v:
                voucher = Voucher(
                    code=v["code"],
                    discount_type=v["discount_type"],
                    discount_value=v["discount_value"],
                    min_order_value=v["min_order_value"],
                    max_discount=v["max_discount"],
                    usage_limit=v["usage_limit"],
                    used_count=0,
                    is_active=True,
                    valid_from=v["valid_from"],
                    valid_to=v["valid_to"],
                )
                db.add(voucher)
                print(
                    f"  [+] Tạo Voucher: {v['code']} (Giới hạn: {v['usage_limit']} lượt)"
                )

        await db.commit()

    print("=" * 60)
    print("[SUCCESS] KHOI TAO DU LIEU THANH CONG!")
    print("=" * 60)
    print("Tai khoan mau de kiem thu he thong:")
    print(" - Admin:     admin@foodhub.com          | pass: admin123")
    print(" - Merchant:  merchant_pho@foodhub.com    | pass: merchant123")
    print(" - Merchant:  merchant_comtam@foodhub.com | pass: merchant123")
    print(" - Customer:  customer1@foodhub.com       | pass: customer123")
    print(" - Customer:  customer2@foodhub.com       | pass: customer123")
    print(" - Customer:  customer3@foodhub.com       | pass: customer123")
    print(" - Driver:    driver1@foodhub.com         | pass: driver123")
    print(" - Driver:    driver2@foodhub.com         | pass: driver123")
    print("Vouchers:     FOODHUB50, FREESHIP20K, FLASH5")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(seed())
