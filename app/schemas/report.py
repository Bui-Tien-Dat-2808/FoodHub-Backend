from datetime import date

from pydantic import BaseModel


class TopSellingItem(BaseModel):
    menu_item_id: int
    name: str
    total_quantity: int
    total_revenue: float

class RevenueReportResponse(BaseModel):
    restaurant_id: int | None
    start_date: date
    end_date: date
    total_orders: int
    total_revenue: float
    total_food_amount: int
    total_delivery_fees: float
    total_discount_amount: float
    average_order_value: float
    top_selling_items: list[TopSellingItem]