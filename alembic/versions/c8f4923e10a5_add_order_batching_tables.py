"""add_order_batching_tables

Revision ID: c8f4923e10a5
Revises: b7e2311c9f42
Create Date: 2026-09-23 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8f4923e10a5'
down_revision: str | Sequence[str] | None = 'b7e2311c9f42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema to support order batching."""
    # 1. Tạo bảng delivery_batches
    op.create_table(
        'delivery_batches',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('batch_code', sa.String(length=50), nullable=False),
        sa.Column('restaurant_id', sa.Integer(), nullable=False),
        sa.Column('driver_id', sa.Integer(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('PENDING', 'ASSIGNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', name='batchstatus'),
            nullable=False,
        ),
        sa.Column('total_distance_km', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['driver_id'], ['driver_profiles.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_delivery_batches_batch_code', 'delivery_batches', ['batch_code'], unique=True)
    op.create_index('ix_delivery_batches_restaurant_id', 'delivery_batches', ['restaurant_id'], unique=False)
    op.create_index('ix_delivery_batches_driver_id', 'delivery_batches', ['driver_id'], unique=False)
    op.create_index('ix_delivery_batches_status', 'delivery_batches', ['status'], unique=False)

    # 2. Tạo bảng batch_waypoints
    op.create_table(
        'batch_waypoints',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column(
            'waypoint_type',
            sa.Enum('PICKUP', 'DROPOFF', name='waypointtype'),
            nullable=False,
        ),
        sa.Column('target_lat', sa.Float(), nullable=False),
        sa.Column('target_lng', sa.Float(), nullable=False),
        sa.Column('target_address', sa.String(length=255), nullable=False),
        sa.Column('is_completed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['batch_id'], ['delivery_batches.id']),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_batch_waypoints_batch_id', 'batch_waypoints', ['batch_id'], unique=False)
    op.create_index('ix_batch_waypoints_order_id', 'batch_waypoints', ['order_id'], unique=False)

    # 3. Bổ sung cột batch_id vào delivery_assignments
    op.add_column(
        'delivery_assignments',
        sa.Column('batch_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_delivery_assignments_batch_id',
        'delivery_assignments',
        'delivery_batches',
        ['batch_id'],
        ['id'],
    )
    op.create_index('ix_delivery_assignments_batch_id', 'delivery_assignments', ['batch_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_delivery_assignments_batch_id', 'delivery_assignments', type_='foreignkey')
    op.drop_index('ix_delivery_assignments_batch_id', table_name='delivery_assignments')
    op.drop_column('delivery_assignments', 'batch_id')

    op.drop_index('ix_batch_waypoints_order_id', table_name='batch_waypoints')
    op.drop_index('ix_batch_waypoints_batch_id', table_name='batch_waypoints')
    op.drop_table('batch_waypoints')

    op.drop_index('ix_delivery_batches_status', table_name='delivery_batches')
    op.drop_index('ix_delivery_batches_driver_id', table_name='delivery_batches')
    op.drop_index('ix_delivery_batches_restaurant_id', table_name='delivery_batches')
    op.drop_index('ix_delivery_batches_batch_code', table_name='delivery_batches')
    op.drop_table('delivery_batches')

