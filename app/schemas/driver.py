from pydantic import BaseModel, Field


class DriverLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Vĩ độ")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Kinh độ")