from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.restaurant import Restaurant
from app.schemas.pricing import DeliveryFeeEstimateResponse
from app.services.geo_service import haversine_distance
from app.services.pricing_service import calculate_delivery_fee, compute_dynamic_surge

router = APIRouter()


@router.get("/estimate-delivery", response_model=DeliveryFeeEstimateResponse)
async def estimate_delivery_fee(
    delivery_lat: Annotated[float, Query(..., ge=-90.0, le=90.0, description="Vĩ độ điểm nhận món")],
    delivery_lng: Annotated[float, Query(..., ge=-180.0, le=180.0, description="Kinh độ điểm nhận món")],
    db: Annotated[AsyncSession, Depends(get_db)],
    restaurant_id: int | None = Query(None, description="ID nhà hàng đặt món"),
    origin_lat: float | None = Query(None, ge=-90.0, le=90.0, description="Vĩ độ điểm lấy hàng (nếu không có restaurant_id)"),
    origin_lng: float | None = Query(None, ge=-180.0, le=180.0, description="Kinh độ điểm lấy hàng (nếu không có restaurant_id)"),
):
    """
    Ước tính phí giao hàng thời gian thực:
    - Tính khoảng cách Haversine từ nhà hàng đến điểm nhận.
    - Áp dụng thuật toán Surge Pricing động theo khung giờ cao điểm và tỷ lệ cung/cầu.
    """
    if restaurant_id is not None:
        stmt = select(Restaurant).where(Restaurant.id == restaurant_id)
        restaurant = (await db.execute(stmt)).scalar_one_or_none()
        if not restaurant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy nhà hàng",
            )
        start_lat = restaurant.latitude
        start_lng = restaurant.longitude
    elif origin_lat is not None and origin_lng is not None:
        start_lat = origin_lat
        start_lng = origin_lng
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cần cung cấp restaurant_id hoặc tọa độ điểm lấy món (origin_lat, origin_lng)",
        )

    distance_km = haversine_distance(start_lat, start_lng, delivery_lat, delivery_lng)
    surge_multiplier, surge_reason = await compute_dynamic_surge(db, start_lat, start_lng)

    base_fee = calculate_delivery_fee(distance_km, surge_multiplier=1.0)
    final_fee = calculate_delivery_fee(distance_km, surge_multiplier=surge_multiplier)

    return DeliveryFeeEstimateResponse(
        distance_km=distance_km,
        base_delivery_fee=base_fee,
        surge_multiplier=surge_multiplier,
        surge_reason=surge_reason,
        final_delivery_fee=final_fee,
    )

