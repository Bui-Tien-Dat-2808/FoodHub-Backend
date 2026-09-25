from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CheckoutSagaRequest(BaseModel):
    payment_method: str = Field(default="MOCK_WALLET", description="Phương thức thanh toán: MOCK_WALLET, BANKING, COD")
    simulate_payment_failure: bool = Field(default=False, description="Mô phỏng sự cố thanh toán để kiểm tra bù trừ Saga")


class SagaInstanceResponse(BaseModel):
    id: int
    saga_id: str
    saga_type: str
    order_id: int
    status: str
    current_step: str
    payload_json: str
    compensation_log_json: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SagaExecutionResult(BaseModel):
    saga_id: str
    order_id: int
    status: str
    current_step: str
    steps_completed: list[str]
    is_successful: bool
    message: str
    compensation_log: list[dict[str, Any]] = []
    error: str | None = None
