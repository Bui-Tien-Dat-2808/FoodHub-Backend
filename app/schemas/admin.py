from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RefundRequest(BaseModel):
    amount: int | None = Field(None, ge=1000, description="Số tiền hoàn")
    reason: str = Field(..., min_length=5, max_length=255, description="Lý do hoàn tiền")

class RefundResponse(BaseModel):
    order_id: int
    refund_amount: int
    reason: str
    status: str
    refunded_at: datetime

class AuditLogResponse(BaseModel):
    id: int
    actor_id: int | None
    action: str
    entity: str
    entity_id: int
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)