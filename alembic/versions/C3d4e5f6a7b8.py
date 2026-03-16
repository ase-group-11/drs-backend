"""fix road_segments unique constraint to segment_id + disaster_id

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-15

Road segments should be unique per (segment_id, disaster_id) pair,
not just segment_id. A physical road can be blocked by multiple
disasters at different times without overwriting historical records.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing unique constraint on segment_id alone
    op.drop_constraint('road_segments_segment_id_key', 'road_segments', type_='unique')

    # Add new unique constraint on (segment_id, disaster_id) pair
    op.create_unique_constraint(
        'uq_road_segments_segment_disaster',
        'road_segments',
        ['segment_id', 'disaster_id']
    )


def downgrade() -> None:
    op.drop_constraint('uq_road_segments_segment_disaster', 'road_segments', type_='unique')
    op.create_unique_constraint('road_segments_segment_id_key', 'road_segments', ['segment_id'])