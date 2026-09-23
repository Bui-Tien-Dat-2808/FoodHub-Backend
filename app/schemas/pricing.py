from pydantic import BaseModel, Field


class DeliveryFeeEstimateResponse(BaseModel):
    distance_km: float = Field(..., description="Khoảng cách tính theo km")
    base_delivery_fee: int = Field(..., description="Phí giao hàng cơ bản (chưa nhân hệ số surge)")
    surge_multiplier: float = Field(..., description="Hệ số phụ thu cao điểm (1.0 là bình thường)")
    surge_reason: str = Field(..., description="Lý do áp dụng phụ thu (khung giờ cao điểm hoặc nhu cầu)")
    final_delivery_fee: int = Field(..., description="Phí giao hàng thực tế cuối cùng")

