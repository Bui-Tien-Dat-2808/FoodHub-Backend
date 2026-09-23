from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import LedgerAccountType, LedgerEntryType


class LedgerEntryResponse(BaseModel):
    id: int
    order_id: int
    account_type: LedgerAccountType
    entry_type: LedgerEntryType
    amount: int
    description: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReconciliationItem(BaseModel):
    order_id: int
    order_code: str
    subtotal: int
    commission_rate: float
    commission_amount: int
    net_payout: int
    settled_at: datetime


class RestaurantReconciliationResponse(BaseModel):
    restaurant_id: int
    restaurant_name: str
    total_settled_orders: int = Field(..., description="Tổng số đơn hàng hoàn thành đã đối soát")
    total_gross_food: int = Field(..., description="Tổng doanh số món ăn (Gross)")
    total_commission_fee: int = Field(..., description="Tổng phí hoa hồng chiết khấu nền tảng")
    total_net_payout: int = Field(..., description="Tổng tiền thực nhận của nhà hàng (Net Payout)")
    items: list[ReconciliationItem] = Field(default=[], description="Chi tiết các đơn hàng đối soát")

