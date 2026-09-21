from fastapi import HTTPException, status

from app.models.enums import OrderStatus, UserRole

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.SUBMITTED: {OrderStatus.MERCHANT_ACCEPTED, OrderStatus.CANCELLED},
    OrderStatus.MERCHANT_ACCEPTED: {OrderStatus.PREPARING, OrderStatus.CANCELLED},
    OrderStatus.PREPARING: {OrderStatus.READY_FOR_PICKUP},
    OrderStatus.READY_FOR_PICKUP: {OrderStatus.DRIVER_ASSIGNED, OrderStatus.CANCELLED},
    OrderStatus.DRIVER_ASSIGNED: {OrderStatus.PICKED_UP, OrderStatus.READY_FOR_PICKUP},
    OrderStatus.PICKED_UP: {OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED_DELIVERY: set(),
}

# Phân quyền RBAC
ROLE_PERMISSIONS: dict[UserRole, set[tuple[OrderStatus, OrderStatus]]] = {
    UserRole.CUSTOMER: {
        (OrderStatus.SUBMITTED, OrderStatus.CANCELLED), # Khách chỉ được huỷ khi quán chưa nhận
    },

    UserRole.MERCHANT: {
        (OrderStatus.SUBMITTED, OrderStatus.MERCHANT_ACCEPTED),
        (OrderStatus.MERCHANT_ACCEPTED, OrderStatus.PREPARING),
        (OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP),
        (OrderStatus.SUBMITTED, OrderStatus.CANCELLED),
        (OrderStatus.MERCHANT_ACCEPTED, OrderStatus.CANCELLED),
    },

    UserRole.DRIVER: {
        (OrderStatus.READY_FOR_PICKUP, OrderStatus.DRIVER_ASSIGNED),
        (OrderStatus.DRIVER_ASSIGNED, OrderStatus.PICKED_UP),
        (OrderStatus.DRIVER_ASSIGNED, OrderStatus.READY_FOR_PICKUP),
        (OrderStatus.PICKED_UP, OrderStatus.DELIVERED),
        (OrderStatus.PICKED_UP, OrderStatus.FAILED_DELIVERY),
    }
}

def validate_state_transition(
        current_status: OrderStatus,
        target_status: OrderStatus,
        actor_role: UserRole
) -> None:
    # 1. Kiểm tra tính hợp lệ của State Machine
    allowed_targets = VALID_TRANSITIONS.get(current_status, set())
    if target_status not in allowed_targets:
        raise HTTPException (
            status_code = status.HTTP_400_BAD_REQUEST, detail=f"Quy tình không hợp lệ: Không thể chuyển từ '{current_status.value}' sang '{target_status.value}'"
        )

    # 2. Kiểm tra quyền của người kích hoạt (Admin được toàn quyền can thiệp)
    if actor_role != UserRole.ADMIN:
        allowed_pairs = ROLE_PERMISSIONS.get(actor_role, set())
        if (current_status, target_status) not in allowed_pairs:
            raise HTTPException (
                status_code=status.HTTP_403_FORBIDDEN, detail=f"Vai trò '{actor_role.value}' không có quyền thực hiện hành động này"
            )