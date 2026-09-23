from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    address: str = Field(min_length=5, max_length=255)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    delivery_radius_km: float = Field(
        default=10.0,
        ge=0.5,
        le=50.0,
        description="Bán kính giao hàng tối đa tính bằng km",
    )


class RestaurantUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    is_open: bool | None = None
    delivery_radius_km: float | None = Field(
        default=None,
        ge=0.5,
        le=50.0,
        description="Cập nhật bán kính giao hàng (km)",
    )


class DeliveryZoneUpdate(BaseModel):
    delivery_radius_km: float = Field(
        ...,
        ge=0.5,
        le=50.0,
        description="Bán kính phục vụ tối đa mới của quán ăn (km)",
    )


class RestaurantResponse(BaseModel):
    id: int
    owner_id: int
    name: str
    address: str
    latitude: float
    longitude: float
    delivery_radius_km: float = 10.0
    is_open: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)