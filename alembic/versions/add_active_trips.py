"""add_active_trips_table

COPY TO: alembic/versions/002_add_active_trips.py
Fill in down_revision with your latest revision ID from: alembic history

Run: alembic upgrade head
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "add_active_trips"
down_revision = "add_geometry_roadsegments" 
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "active_trips",

        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),

        # One trip per user — conflict target for upsert
        sa.Column("user_id", sa.String(), nullable=False),

        # Current GPS
        sa.Column("current_lat", sa.Float(), nullable=False),
        sa.Column("current_lng", sa.Float(), nullable=False),

        # Destination
        sa.Column("dest_lat", sa.Float(), nullable=False),
        sa.Column("dest_lng", sa.Float(), nullable=False),

        # general | public_transport | emergency
        sa.Column("vehicle_type", sa.String(), nullable=False, server_default="general"),

        # Auto-expire after 4 hours
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now() + interval '4 hours'")),

        # Base fields from Base model
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_unique_constraint("uq_active_trips_user_id", "active_trips", ["user_id"])
    op.create_index("ix_active_trips_user_id", "active_trips", ["user_id"])
    op.create_index("ix_active_trips_lat_lng", "active_trips", ["current_lat", "current_lng"])
    op.create_index("ix_active_trips_expires", "active_trips", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_active_trips_expires", table_name="active_trips")
    op.drop_index("ix_active_trips_lat_lng", table_name="active_trips")
    op.drop_index("ix_active_trips_user_id", table_name="active_trips")
    op.drop_constraint("uq_active_trips_user_id", "active_trips")
    op.drop_table("active_trips")