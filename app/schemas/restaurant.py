from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    address: str = Field(min_length=5, max_length=255)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)

class RestaurantUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    is_open: bool | None = None

class RestaurantResponse(BaseModel):
    id: int
    owner_id: int
    name: str
    address: str
    latitude: float
    longitude: float
    is_open: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)