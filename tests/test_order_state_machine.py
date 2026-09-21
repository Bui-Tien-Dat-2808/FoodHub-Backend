from enum import Enum

import pytest


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    MERCHANT_ACCEPTED = "MERCHANT_ACCEPTED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    FAILED_DELIVERY = "FAILED_DELIVERY"


VALID_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
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


class InvalidOrderStateTransition(Exception):
    pass


def validate_order_transition(current_status: OrderStatus, target_status: OrderStatus) -> bool:
    """Kiểm tra tính hợp lệ của việc chuyển trạng thái đơn hàng."""
    allowed = VALID_ORDER_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise InvalidOrderStateTransition(
            f"Không thể chuyển trạng thái từ '{current_status.value}' sang '{target_status.value}'"
        )
    return True


# --- TESTS ĐỂ KIỂM CHỨNG CODE TAY CỦA BẠN ---

def test_valid_lifecycle_happy_path():
    assert validate_order_transition(OrderStatus.SUBMITTED, OrderStatus.MERCHANT_ACCEPTED) is True
    assert validate_order_transition(OrderStatus.MERCHANT_ACCEPTED, OrderStatus.PREPARING) is True
    assert validate_order_transition(OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP) is True
    assert validate_order_transition(OrderStatus.READY_FOR_PICKUP, OrderStatus.DRIVER_ASSIGNED) is True
    assert validate_order_transition(OrderStatus.DRIVER_ASSIGNED, OrderStatus.PICKED_UP) is True
    assert validate_order_transition(OrderStatus.PICKED_UP, OrderStatus.DELIVERED) is True

def test_invalid_skip_step():
    with pytest.raises(InvalidOrderStateTransition):
        validate_order_transition(OrderStatus.SUBMITTED, OrderStatus.DELIVERED)

def test_cannot_cancel_when_food_in_transit():
    # Khi tài xế đã lấy hàng (PICKED_UP), không được phép hủy ngang
    with pytest.raises(InvalidOrderStateTransition):
        validate_order_transition(OrderStatus.PICKED_UP, OrderStatus.CANCELLED)

def test_terminal_state_cannot_transition():
    # Đã giao xong thì không được chuyển trạng thái nữa
    with pytest.raises(InvalidOrderStateTransition):
        validate_order_transition(OrderStatus.DELIVERED, OrderStatus.CANCELLED)
