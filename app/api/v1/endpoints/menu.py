import json
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.enums import UserRole
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User
from app.schemas.menu import MenuItemCreate, MenuItemResponse, MenuItemUpdate

router = APIRouter()


@router.post("/restaurants/{restaurant_id}/items", response_model=MenuItemResponse, status_code=status.HTTP_201_CREATED)
async def add_menu_item(
    restaurant_id: int,
    item_in: MenuItemCreate,
    current_user: Annotated[User, Depends(require_roles([UserRole.MERCHANT, UserRole.ADMIN]))],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)]
):
    """Thêm món ăn vào menu quán (chỉ chủ quán hoặc Admin)."""
    restaurant = (await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))).scalar_one_or_none()
    if not restaurant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy nhà hàng.")
    if current_user.role != UserRole.ADMIN and restaurant.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền quản lý menu quán ăn này.")

    item = MenuItem(
        restaurant_id=restaurant_id,
        name=item_in.name,
        description=item_in.description,
        base_price=item_in.base_price,
        stock_quantity=item_in.stock_quantity,
        is_available=item_in.is_available,
    )
    db.add(item)
    await db.commit()
    try:
        await redis.delete(f"menu:restaurant:{restaurant_id}")
    except aioredis.RedisError:
        pass

    await db.refresh(item)
    return item


@router.get("/restaurants/{restaurant_id}/items", response_model=list[MenuItemResponse])
async def list_menu_items(
    restaurant_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)]
):
    """
        Khách hàng xem danh sách món ăn của quán.
        Kiểm tra Redis trước, nếu miss mới query DB và cache lại
    """
    cache_key = f"menu:restaurant:{restaurant_id}"

    # 1. Kiểm tra Cache Hit
    try:
        cached_menu = await redis.get(cache_key)
        if cached_menu:
            return json.loads(cached_menu)
    except aioredis.RedisError:
        pass

    # 2. Cache Miss: Đọc data từ PostgreSQL
    stmt = select(MenuItem).where(MenuItem.restaurant_id == restaurant_id, MenuItem.is_available.is_(True))
    result = await db.execute(stmt)
    items = list(result.scalars().all())

    # 3. Ghi kết quả vào Redis với TTL = 3600s
    try:
        serialized_items = [
            MenuItemResponse.model_validate(item).model_dump()
            for item in items
        ]
        await redis.set(cache_key, json.dumps(serialized_items), ex=3600)
    except aioredis.RedisError:
        pass

    return items

@router.get("/items/{item_id}", response_model=MenuItemResponse)
async def get_menu_item(
    item_id: int,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    """Xem chi tiết một món ăn."""
    item = (await db.execute(select(MenuItem).where(MenuItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy món ăn.")
    return item


@router.patch("/items/{item_id}", response_model=MenuItemResponse)
async def update_menu_item(
    item_id: int,
    item_in: MenuItemUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)]
):
    """Chỉnh sửa thông tin món ăn, giá tiền, tồn kho (chủ quán hoặc Admin)."""
    item = (await db.execute(select(MenuItem).where(MenuItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy món ăn.")

    restaurant = (await db.execute(select(Restaurant).where(Restaurant.id == item.restaurant_id))).scalar_one_or_none()
    if current_user.role != UserRole.ADMIN and (not restaurant or restaurant.owner_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền chỉnh sửa món ăn này.")

    if item_in.name is not None:
        item.name = item_in.name
    if item_in.description is not None:
        item.description = item_in.description
    if item_in.base_price is not None:
        item.base_price = item_in.base_price
    if item_in.stock_quantity is not None:
        item.stock_quantity = item_in.stock_quantity
    if item_in.is_available is not None:
        item.is_available = item_in.is_available

    await db.commit()

    try:
        await redis.delete(f"menu:restaurant:{item.restaurant_id}")
    except aioredis.RedisError:
        pass

    await db.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_menu_item(
    item_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)]
):
    """Xóa món ăn khỏi menu (chủ quán hoặc Admin)."""
    item = (await db.execute(select(MenuItem).where(MenuItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy món ăn.")

    restaurant = (await db.execute(select(Restaurant).where(Restaurant.id == item.restaurant_id))).scalar_one_or_none()
    if current_user.role != UserRole.ADMIN and (not restaurant or restaurant.owner_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền xóa món ăn này.")

    rest_id = item.restaurant_id

    await db.delete(item)
    await db.commit()

    try:
        await redis.delete(f"menu:restaurant:{rest_id}")
    except aioredis.RedisError:
        pass
    return None
