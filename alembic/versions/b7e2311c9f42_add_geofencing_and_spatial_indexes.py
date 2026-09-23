"""add_geofencing_and_spatial_indexes

Revision ID: b7e2311c9f42
Revises: 45cfdae590c2
Create Date: 2026-09-23 11:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e2311c9f42'
down_revision: str | Sequence[str] | None = '45cfdae590c2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Thêm trường delivery_radius_km vào restaurants (mặc định 10.0 km)
    op.add_column(
        'restaurants',
        sa.Column('delivery_radius_km', sa.Float(), server_default='10.0', nullable=False),
    )

    # 2. Tạo composite index ix_driver_profiles_spatial tối ưu hoá truy vấn định vị tài xế
    op.create_index(
        'ix_driver_profiles_spatial',
        'driver_profiles',
        ['is_online', 'is_busy', 'current_lat', 'current_lng'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_driver_profiles_spatial', table_name='driver_profiles')
    op.drop_column('restaurants', 'delivery_radius_km')

