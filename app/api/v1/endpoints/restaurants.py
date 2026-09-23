from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.restaurant import (
    DeliveryZoneUpdate,
    RestaurantCreate,
    RestaurantResponse,
    RestaurantUpdate,
)

router = APIRouter()

@router.post("/", response_model=RestaurantResponse, status_code=status.HTTP_201_CREATED)
async def create_restaurant(
    restaurant_in: RestaurantCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles([UserRole.MERCHANT, UserRole.ADMIN]))],
):
    """
        Chỉ MERCHANT và ADMIN mới có quyền tạo nhà hàng.
    """
    new_restaurant = Restaurant(
        owner_id=current_user.id,
        name=restaurant_in.name,
        address=restaurant_in.address,
        latitude=restaurant_in.latitude,
        longitude=restaurant_in.longitude,
        delivery_radius_km=restaurant_in.delivery_radius_km,
    )
    db.add(new_restaurant)
    await db.commit()
    await db.refresh(new_restaurant)
    return new_restaurant

@router.get("/", response_model=list[RestaurantResponse])
async def list_restaurants(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = None,
):
    """
        Khách hàng tìm kiếm và lấy danh sách nhà hàng.
    """
    stmt = select(Restaurant)
    if search:
        stmt = stmt.where(Restaurant.name.ilike(f"%{search}%"))
    stmt = stmt.offset(skip).limit(limit)
    result = await db.execute(stmt)
    restaurants = result.scalars().all()
    return restaurants

@router.get("/{restaurant_id}", response_model=RestaurantResponse)
async def get_restaurant(
    restaurant_id: int, 
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    restaurant = result.scalar_one_or_none()
    if not restaurant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy nhà hàng bạn đang tìm.")
    return restaurant

@router.patch("/{restaurant_id}", response_model=RestaurantResponse)
async def update_restaurant(
    restaurant_id: int,
    restaurant_in: RestaurantUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Cập nhật thông tin nhà hàng (chủ quán hoặc Admin)."""
    restaurant = (await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))).scalar_one_or_none()
    if not restaurant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy nhà hàng bạn đang tìm.")
    if current_user.role != UserRole.ADMIN and restaurant.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền chỉnh sửa nhà hàng này.")

    if restaurant_in.name is not None:
        restaurant.name = restaurant_in.name
    if restaurant_in.address is not None:
        restaurant.address = restaurant_in.address
    if restaurant_in.is_open is not None:
        restaurant.is_open = restaurant_in.is_open
    if restaurant_in.delivery_radius_km is not None:
        restaurant.delivery_radius_km = restaurant_in.delivery_radius_km

    await db.commit()
    await db.refresh(restaurant)
    return restaurant


@router.patch("/{restaurant_id}/delivery-zone", response_model=RestaurantResponse)
async def update_restaurant_delivery_zone(
    restaurant_id: int,
    zone_in: DeliveryZoneUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Cập nhật bán kính phục vụ / Geofencing của nhà hàng (chỉ chủ quán hoặc Admin).
    """
    restaurant = (await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))).scalar_one_or_none()
    if not restaurant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy nhà hàng bạn đang tìm.")
    if current_user.role != UserRole.ADMIN and restaurant.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bạn không có quyền chỉnh sửa vùng giao hàng của quán này.")

    restaurant.delivery_radius_km = zone_in.delivery_radius_km
    await db.commit()
    await db.refresh(restaurant)
    return restaurant
