from enum import Enum


class UserRole(str, Enum):
    CUSTOMER = "CUSTOMER"
    MERCHANT = "MERCHANT"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"

class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED" # Khách vừa đặt hàng
    MERCHANT_ACCEPTED = "MERCHANT_ACCEPTED" # Quán đã nhận đơn hàng
    PREPARING = "PREPARING" # Quán đang chuẩn bị món
    READY_FOR_PICKUP = "READY_FOR_PICKUP" # Quán đã chuẩn bị xong món, chờ tài xế đến lấy
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED" # Tài xế đã nhận đơn
    PICKED_UP = "PICKED_UP" # Tài xế đã lấy hàng, đang đi giao
    DELIVERED = "DELIVERED" # Giao thành công
    CANCELLED = "CANCELLED" # Đơn hàng bị hủy
    CANCELED = "CANCELLED" # Alias
    FAILED_DELIVERY = "FAILED_DELIVERY" # Giao thất bại (không liên lạc được với khách)

class DiscountType(str, Enum):
    PERCENTAGE = "PERCENTAGE" # Giảm theo %
    FIXED = "FIXED" # Giảm số tiền cố định

class DriverAssignmentStatus(str, Enum):
    ACCEPTED = "ACCEPTED" # Tài xế đã nhận đơn
    ARRIVED_AT_STORE = "ARRIVED_AT_STORE" # Tài xế đã đến quán
    DELIVERING = "DELIVERING" # Tài xế đang giao hàng
    COMPLETED = "COMPLETED" # Tài xế đã giao hàng thành công
    CANCELED = "CANCELED" # Tài xế hủy đơn


class LedgerAccountType(str, Enum):
    CUSTOMER = "CUSTOMER"
    RESTAURANT = "RESTAURANT"
    PLATFORM = "PLATFORM"


class LedgerEntryType(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class BatchStatus(str, Enum):
    PENDING = "PENDING"          # Batch mới tạo, chờ tài xế nhận
    ASSIGNED = "ASSIGNED"        # Tài xế đã nhận batch
    IN_PROGRESS = "IN_PROGRESS"  # Tài xế đang thực hiện lộ trình giao các chặng
    COMPLETED = "COMPLETED"      # Toàn bộ đơn trong batch đã giao thành công
    CANCELLED = "CANCELLED"      # Batch bị huỷ


class WaypointType(str, Enum):
    PICKUP = "PICKUP"    # Điểm lấy hàng tại nhà hàng
    DROPOFF = "DROPOFF"  # Điểm trả hàng cho khách hàng

