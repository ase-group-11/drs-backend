"""Add disaster reporting tables

Revision ID: b4f8a2c9d1e3
Revises: 387605b8a1e4
Create Date: 2026-02-10 17:45:00.000000
"""

from alembic import op
import sqlalchemy as sa
import geoalchemy2


# revision identifiers, used by Alembic.
revision = 'b4f8a2c9d1e3'
down_revision = '387605b8a1e4'
branch_labels = None
depends_on = None


def upgrade():
    # Create disasters table (enums will be created automatically by SQLAlchemy)
    op.create_table('disasters',
        sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, from_text='ST_GeogFromText', name='geography'), nullable=False),
        sa.Column('location_lat', sa.Float(), nullable=False),
        sa.Column('location_lng', sa.Float(), nullable=False),
        sa.Column('disaster_type', sa.Enum('flood', 'fire', 'accident', 'medical', 'other', name='disastertype'), nullable=False),
        sa.Column('severity', sa.Enum('low', 'medium', 'high', 'critical', name='disasterseverity'), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('reporter_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('status', sa.Enum('pending', 'assigned', 'in_progress', 'resolved', 'closed', name='disasterstatus'), nullable=False),
        sa.Column('image_urls', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index(op.f('ix_disasters_location_lat'), 'disasters', ['location_lat'], unique=False)
    op.create_index(op.f('ix_disasters_location_lng'), 'disasters', ['location_lng'], unique=False)
    op.create_index(op.f('ix_disasters_disaster_type'), 'disasters', ['disaster_type'], unique=False)
    op.create_index(op.f('ix_disasters_severity'), 'disasters', ['severity'], unique=False)
    op.create_index(op.f('ix_disasters_status'), 'disasters', ['status'], unique=False)


def downgrade():
    # Drop table and indexes (enums will be dropped automatically by SQLAlchemy)
    op.drop_index(op.f('ix_disasters_status'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_severity'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_disaster_type'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_location_lng'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_location_lat'), table_name='disasters')
    op.drop_table('disasters')
