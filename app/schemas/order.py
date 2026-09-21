from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OrderStatus


class OrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0, description="Số lượng món phải lớn hơn 0")

class OrderCreate(BaseModel):
    restaurant_id: int
    items: list[OrderItemCreate] = Field(min_length=1, description="Đơn hàng phải có ít nhất 1 món")
    voucher_code: str | None = Field(default=None, description="Voucher khuyến mãi (nếu có)")
    delivery_lat: float = Field(ge=-90.0, le=90.0)
    delivery_lng: float = Field(ge=-180.0, le=180.0)
    delivery_address: str = Field(min_length=5, max_length=255)

class OrderItemResponse(BaseModel):
    id: int
    menu_item_id: int
    quantity: int
    unit_price: int
    subtotal: int

    model_config = ConfigDict(from_attributes=True)

class OrderResponse(BaseModel):
    id: int
    order_code: str
    customer_id: int
    restaurant_id: int
    status: OrderStatus
    delivery_address: str
    delivery_lat: float
    delivery_lng: float
    subtotal: int
    delivery_fee: int
    discount_amount: int
    total_amount: int
    created_at: datetime
    items: list[OrderItemResponse]

    model_config = ConfigDict(from_attributes=True)

class OrderStatusUpdate(BaseModel):
    new_status: OrderStatus
    reason: str | None = Field(default=None, max_length=255)

class OrderStatusHistoryResponse(BaseModel):
    id: int
    from_status: str
    to_status: str
    reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class OrderDetailResponse(OrderResponse):
    """
        Trả về cả lịch sử chuyển trạng thái của đơn
    """
    histories: list[OrderStatusHistoryResponse] = []