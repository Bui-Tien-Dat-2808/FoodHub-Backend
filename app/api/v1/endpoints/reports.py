from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.report import RevenueReportResponse
from app.services.report_service import get_revenue_report

router = APIRouter()

@router.get("/revenue", response_model=RevenueReportResponse)
async def get_revenue_statistics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    start_date: date | None = Query(None, description="Ngày bắt đầu (mặc định: 30 ngày trước)"),
    end_date: date | None = Query(None, description="Ngày kết thúc (mặc định: hôm nay)"),
    restaurant_id: int | None = Query(None, description="ID quán ăn (chỉ dành cho Admin)"),
):
    """
        Báo cáo doanh thu & thống kê:
        - MERCHANT: Chỉ xem được báo cáo của quán mình
        - ADMIN: Xem được của quán bất kỳ hoặc ttoanf hệ thống
        - CUSTOMER / DRIVER: Không được xem
    """
    today = datetime.now(UTC).date()
    end = end_date or today
    start = start_date or (end - timedelta(days=30))

    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngày bắt đầu không được lớn hơn ngày kết thúc"
        )

    # 1. Kiểm tra RBAC
    if current_user.role not in (UserRole.MERCHANT, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền thực hiện hành động này"
        )

    target_restaurant_id = None

    if current_user.role == UserRole.MERCHANT:
        # MERCHANT chỉ được xem báo cáo quán mình sở hữu
        stmt = select(Restaurant).where(Restaurant.owner_id == current_user.id)
        rest = (await db.execute(stmt)).scalar_one_or_none()
        if not rest:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bạn chưa tạo nhà hàng nào để xem báo cáo"
            )
        target_restaurant_id = rest.id
    else:
        # Admin có thể truyền restaurant_id hoặc để None để xem toàn bộ nhà hàng
        target_restaurant_id = restaurant_id

    # 2. Gọi service tổng hợp & cache
    report = await get_revenue_report(
        db=db,
        redis=redis,
        restaurant_id=target_restaurant_id,
        start_date=start,
        end_date=end
    )
    return report
