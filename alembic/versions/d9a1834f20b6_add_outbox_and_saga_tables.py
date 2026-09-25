"""add_outbox_and_saga_tables

Revision ID: d9a1834f20b6
Revises: c8f4923e10a5
Create Date: 2026-09-23 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd9a1834f20b6'
down_revision: str | Sequence[str] | None = 'c8f4923e10a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema to add outbox_events and saga_instances tables."""
    # 1. Tạo bảng outbox_events
    op.create_table(
        'outbox_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(length=50), nullable=False),
        sa.Column('aggregate_type', sa.String(length=50), nullable=False),
        sa.Column('aggregate_id', sa.String(length=50), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='PENDING', nullable=False),
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_retries', sa.Integer(), server_default='3', nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_outbox_events_event_id', 'outbox_events', ['event_id'], unique=True)
    op.create_index('ix_outbox_events_aggregate_type', 'outbox_events', ['aggregate_type'], unique=False)
    op.create_index('ix_outbox_events_aggregate_id', 'outbox_events', ['aggregate_id'], unique=False)
    op.create_index('ix_outbox_events_event_type', 'outbox_events', ['event_type'], unique=False)
    op.create_index('ix_outbox_events_status', 'outbox_events', ['status'], unique=False)
    op.create_index('ix_outbox_events_status_created_at', 'outbox_events', ['status', 'created_at'], unique=False)

    # 2. Tạo bảng saga_instances
    op.create_table(
        'saga_instances',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('saga_id', sa.String(length=50), nullable=False),
        sa.Column('saga_type', sa.String(length=50), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='STARTED', nullable=False),
        sa.Column('current_step', sa.String(length=100), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('compensation_log_json', sa.Text(), server_default='[]', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_saga_instances_saga_id', 'saga_instances', ['saga_id'], unique=True)
    op.create_index('ix_saga_instances_saga_type', 'saga_instances', ['saga_type'], unique=False)
    op.create_index('ix_saga_instances_order_id', 'saga_instances', ['order_id'], unique=False)
    op.create_index('ix_saga_instances_status', 'saga_instances', ['status'], unique=False)
    op.create_index('ix_saga_instances_status_created_at', 'saga_instances', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_saga_instances_status_created_at', table_name='saga_instances')
    op.drop_index('ix_saga_instances_status', table_name='saga_instances')
    op.drop_index('ix_saga_instances_order_id', table_name='saga_instances')
    op.drop_index('ix_saga_instances_saga_type', table_name='saga_instances')
    op.drop_index('ix_saga_instances_saga_id', table_name='saga_instances')
    op.drop_table('saga_instances')

    op.drop_index('ix_outbox_events_status_created_at', table_name='outbox_events')
    op.drop_index('ix_outbox_events_status', table_name='outbox_events')
    op.drop_index('ix_outbox_events_event_type', table_name='outbox_events')
    op.drop_index('ix_outbox_events_aggregate_id', table_name='outbox_events')
    op.drop_index('ix_outbox_events_aggregate_type', table_name='outbox_events')
    op.drop_index('ix_outbox_events_event_id', table_name='outbox_events')
    op.drop_table('outbox_events')
