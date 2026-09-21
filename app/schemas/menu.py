from pydantic import BaseModel, ConfigDict, Field


class MenuItemCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str | None = None
    base_price: int = Field(gt=0, description="Giá bán tính theo VNĐ, phải lớn hơn 0")
    stock_quantity: int = Field(ge=0, default=100, description="Số lượng kho trong ngày")
    is_available: bool = True

class MenuItemUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    base_price: int | None = Field(default=None, gt=0)
    stock_quantity: int | None = Field(default=None, ge=0)
    is_available: bool | None = None

class MenuItemResponse(BaseModel):
    id: int
    restaurant_id: int
    name: str
    description: str | None
    base_price: int
    stock_quantity: int
    is_available: bool
    
    model_config = ConfigDict(from_attributes=True)