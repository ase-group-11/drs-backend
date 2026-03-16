"""sync disaster_type enum values

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-14

Adds missing disaster type enum values to match Python DisasterType enum.
PostgreSQL requires ALTER TYPE to add new enum values.
Cannot remove existing values without recreating the type.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add missing enum values that exist in Python but not in DB
    # PostgreSQL ALTER TYPE ADD VALUE is safe and non-destructive
    missing_values = ['HURRICANE', 'TORNADO', 'TSUNAMI', 'DROUGHT', 'HEATWAVE', 'COLDWAVE']
    for value in missing_values:
        op.execute(
            f"ALTER TYPE disaster_type ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    # downgrade is a no-op
    pass