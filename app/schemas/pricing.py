from pydantic import BaseModel, Field


class DeliveryFeeEstimateResponse(BaseModel):
    distance_km: float = Field(..., description="Khoảng cách tính theo km")
    base_delivery_fee: int = Field(..., description="Phí giao hàng cơ bản (chưa nhân hệ số surge)")
    surge_multiplier: float = Field(..., description="Hệ số phụ thu cao điểm (1.0 là bình thường)")
    surge_reason: str = Field(..., description="Lý do áp dụng phụ thu (khung giờ cao điểm hoặc nhu cầu)")
    final_delivery_fee: int = Field(..., description="Phí giao hàng thực tế cuối cùng")

    is_within_delivery_zone: bool = Field(
        default=True,
        description="Toạ độ giao hàng có nằm trong bán kính phục vụ của quán hay không",
    )
    max_delivery_radius_km: float | None = Field(
        default=None,
        description="Bán kính phục vụ tối đa của quán (km)",
    )
