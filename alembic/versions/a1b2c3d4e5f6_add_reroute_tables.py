"""add reroute tables: road_segments, reroute_plans, traffic_overrides, audit_logs

Revision ID: a1b2c3d4e5f6
Revises: 464c978e238b
Create Date: 2026-03-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = 'a1b2c3d4e5f6'
down_revision = '2d27c79cbad9'
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ------------------------------------------------------------------
    # road_segments
    # ------------------------------------------------------------------
    op.create_table(
        'road_segments',
        sa.Column('id', UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),

        sa.Column('segment_id', sa.String(200), nullable=False, unique=True),
        sa.Column('road_name', sa.String(500), nullable=True),
        sa.Column('start_lat', sa.Float(), nullable=False),
        sa.Column('start_lng', sa.Float(), nullable=False),
        sa.Column('end_lat', sa.Float(), nullable=False),
        sa.Column('end_lng', sa.Float(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('reason', sa.String(200), nullable=True),
        sa.Column('disaster_id', UUID(as_uuid=False),
                  sa.ForeignKey('disasters.id', ondelete='SET NULL'), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=False, server_default='300'),
    )
    op.create_index('idx_road_segments_status', 'road_segments', ['status'])
    op.create_index('idx_road_segments_disaster', 'road_segments', ['disaster_id'])
    op.create_index('idx_road_segments_segment_id', 'road_segments', ['segment_id'])

    # ------------------------------------------------------------------
    # reroute_plans
    # ------------------------------------------------------------------
    op.create_table(
        'reroute_plans',
        sa.Column('id', UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),

        sa.Column('disaster_id', UUID(as_uuid=False),
                  sa.ForeignKey('disasters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('blocked_roads', sa.JSON(), nullable=False),
        sa.Column('route_assignments', sa.JSON(), nullable=False),
        sa.Column('estimated_times', sa.JSON(), nullable=True),
        sa.Column('capacity_usage', sa.JSON(), nullable=True),
        sa.Column('chosen_routes', sa.JSON(), nullable=True),
        sa.Column('trigger_source', sa.String(50), nullable=False, server_default='disaster_trigger'),
        sa.Column('vehicles_affected', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('idx_reroute_plans_disaster_status', 'reroute_plans', ['disaster_id', 'status'])
    op.create_index('idx_reroute_plans_status', 'reroute_plans', ['status'])

    # ------------------------------------------------------------------
    # traffic_overrides
    # ------------------------------------------------------------------
    op.create_table(
        'traffic_overrides',
        sa.Column('id', UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),

        sa.Column('disaster_id', UUID(as_uuid=False),
                  sa.ForeignKey('disasters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('override_type', sa.String(50), nullable=False),
        sa.Column('segment_id', sa.String(200), nullable=True),
        sa.Column('route_id', sa.String(200), nullable=True),
        sa.Column('priority', sa.String(50), nullable=True),
        sa.Column('operator_id', sa.String(200), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('override_metadata', sa.JSON(), nullable=True),
    )
    op.create_index('idx_traffic_overrides_disaster_active', 'traffic_overrides', ['disaster_id', 'is_active'])
    op.create_index('idx_traffic_overrides_operator', 'traffic_overrides', ['operator_id'])

    # ------------------------------------------------------------------
    # audit_logs
    # ------------------------------------------------------------------
    op.create_table(
        'audit_logs',
        sa.Column('id', UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),

        sa.Column('disaster_id', UUID(as_uuid=False),
                  sa.ForeignKey('disasters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('reroute_plan_id', UUID(as_uuid=False),
                  sa.ForeignKey('reroute_plans.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_data', sa.JSON(), nullable=True),
        sa.Column('triggered_by', sa.String(100), nullable=False, server_default='system'),
    )
    op.create_index('idx_audit_logs_disaster_event', 'audit_logs', ['disaster_id', 'event_type'])
    op.create_index('idx_audit_logs_event_type', 'audit_logs', ['event_type'])
    op.create_index('idx_audit_logs_created', 'audit_logs', ['created_at'])


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('traffic_overrides')
    op.drop_table('reroute_plans')
    op.drop_table('road_segments')