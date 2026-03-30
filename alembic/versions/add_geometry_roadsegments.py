"""add geometry columns to road_segments

Revision ID: add_geometry_roadsegments
Revises: a1b2c3d4e5f6
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'add_geometry_roadsegments'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('road_segments',
        sa.Column('points', JSONB, nullable=True,
                  comment='Road-following polyline [[lat,lng],...] from TomTom')
    )
    op.add_column('road_segments',
        sa.Column('geojson', JSONB, nullable=True,
                  comment='GeoJSON LineString Feature for this segment')
    )


def downgrade() -> None:
    op.drop_column('road_segments', 'geojson')
    op.drop_column('road_segments', 'points')