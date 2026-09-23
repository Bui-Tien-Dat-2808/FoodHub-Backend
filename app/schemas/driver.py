from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DriverAssignmentStatus


class DriverLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Vĩ độ")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Kinh độ")


class DriverStatusUpdate(BaseModel):
    is_online: bool = Field(..., description="Trạng thái sẵn sàng nhận đơn")


class DriverProfileResponse(BaseModel):
    id: int
    user_id: int
    license_plate: str
    current_lat: float | None = None
    current_lng: float | None = None
    is_online: bool
    is_busy: bool
    rating: float

    model_config = ConfigDict(from_attributes=True)


class DriverAssignmentResponse(BaseModel):
    id: int
    order_id: int
    driver_id: int
    status: DriverAssignmentStatus
    claimed_at: datetime
    delivered_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class NearbyDriverResponse(BaseModel):
    driver_id: int
    user_id: int
    full_name: str
    phone_number: str | None = None
    license_plate: str
    distance_km: float
    current_lat: float
    current_lng: float
    rating: float


class AutoAssignResponse(BaseModel):
    order_id: int
    driver_id: int
    driver_name: str
    license_plate: str
    distance_km: float
    status: str
    message: str