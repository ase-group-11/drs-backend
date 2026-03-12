"""updated emergency model (stub - original file missing, changes already applied)

Revision ID: 2d27c79cbad9
Revises: 6f4781f357f9
Create Date: 2026-02-19 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2d27c79cbad9'
down_revision = '6f4781f357f9'
branch_labels = None
depends_on = None


def upgrade():
    # Original migration file was lost.
    # Changes from this migration are already applied to the database.
    # This stub exists solely to restore the revision chain so Alembic
    # can locate this revision ID and proceed to newer migrations.
    pass


def downgrade():
    # Cannot safely downgrade - original operations unknown.
    pass
