"""merge all heads

Revision ID: 224596e82083
Revises: 493f4be60325, add_active_trips, c0538541bb7d
Create Date: 2026-03-28 19:20:33.993657
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '224596e82083'
down_revision = ('493f4be60325', 'add_active_trips', 'c0538541bb7d')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
