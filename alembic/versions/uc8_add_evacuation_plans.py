"""Add evacuation_plans table — UC8

Revision ID: uc8_evacuation_plans
Revises: <REPLACE_WITH_YOUR_CURRENT_HEAD>
Create Date: 2026-03-22

HOW TO USE:
  1. Run: alembic history
     Find the line with "(head)" and copy that revision ID.
  2. Replace <REPLACE_WITH_YOUR_CURRENT_HEAD> in down_revision below.
  3. Run: alembic upgrade head
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision      = "uc8_evacuation_plans"
down_revision = None          # ← REPLACE with your current head
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "evacuation_plans",

        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),

        # Human-readable ref: EVA-0001, EVA-0002, …
        sa.Column("plan_ref", sa.String(20), nullable=False, unique=True),

        # Which disaster
        sa.Column("disaster_id", UUID(as_uuid=True),
                  sa.ForeignKey("disasters.id", ondelete="CASCADE"), nullable=False),

        # Lifecycle: PENDING → APPROVED → ACTIVE → COMPLETED
        sa.Column("plan_status", sa.String(20), nullable=False, default="PENDING"),

        # ── Phase 1 JSONB data ───────────────────────────────────────────────
        sa.Column("impact_zones",          JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("population_stats",      JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("blocked_roads",         JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("traffic_snapshot",      JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("shelters_with_capacity",JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("best_routes_per_zone",  JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("transport_plan",        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("allocations",           JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),

        # ── Phase 4 data ─────────────────────────────────────────────────────
        sa.Column("completion_metrics",    JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),

        # ── Approval / activation metadata ───────────────────────────────────
        sa.Column("auto_approved", sa.Boolean, nullable=False, default=False),
        sa.Column("approved_by",   sa.String(150), nullable=True),
        sa.Column("approved_at",   sa.DateTime,    nullable=True),
        sa.Column("activated_at",  sa.DateTime,    nullable=True),
        sa.Column("completed_at",  sa.DateTime,    nullable=True),
        sa.Column("notes",         sa.Text,        nullable=True),

        # ── Audit ─────────────────────────────────────────────────────────────
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )

    op.create_index("idx_evacuation_plans_disaster_id", "evacuation_plans", ["disaster_id"])
    op.create_index("idx_evacuation_plans_status",      "evacuation_plans", ["plan_status"])
    op.create_index("idx_evacuation_plans_plan_ref",    "evacuation_plans", ["plan_ref"], unique=True)
    op.create_index(
        "idx_evacuation_plans_active",
        "evacuation_plans", ["plan_status"],
        postgresql_where=sa.text("plan_status IN ('APPROVED','ACTIVE','MONITORING')"),
    )


def downgrade() -> None:
    op.drop_index("idx_evacuation_plans_active",      table_name="evacuation_plans")
    op.drop_index("idx_evacuation_plans_plan_ref",    table_name="evacuation_plans")
    op.drop_index("idx_evacuation_plans_status",      table_name="evacuation_plans")
    op.drop_index("idx_evacuation_plans_disaster_id", table_name="evacuation_plans")
    op.drop_table("evacuation_plans")
