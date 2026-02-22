"""add disaster_evaluations table

Revision ID: a1b2c3d4e5f6
Revises: 3bf1bc535733
Create Date: 2026-02-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'a1b2c3d4e5f6'
down_revision = '3bf1bc535733'
branch_labels = None
depends_on = None


def upgrade():
    # Create evaluation_flag enum (checkfirst avoids errors on re-run)
    evaluation_flag_enum = sa.Enum(
        'normal', 'limited_data', 'false_alarm', 'pending_review',
        name='evaluation_flag',
    )
    evaluation_flag_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'disaster_evaluations',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'report_id',
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey('disaster_reports.id', ondelete='CASCADE'),
            nullable=False,
            comment='Report this evaluation belongs to',
        ),
        sa.Column(
            'severity',
            sa.Enum('low', 'medium', 'high', 'critical', name='disaster_severity'),
            nullable=False,
            comment='Evaluated severity level',
        ),
        sa.Column(
            'confidence',
            sa.Float(),
            nullable=False,
            comment='Confidence score [0.0, 1.0]',
        ),
        sa.Column(
            'recommended_services',
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            comment='Recommended emergency services',
        ),
        sa.Column('trigger_deploy', sa.Boolean(), nullable=False, default=False),
        sa.Column('trigger_reroute', sa.Boolean(), nullable=False, default=False),
        sa.Column('trigger_evacuation', sa.Boolean(), nullable=False, default=False),
        sa.Column(
            'flag',
            sa.Enum('normal', 'limited_data', 'false_alarm', 'pending_review', name='evaluation_flag'),
            nullable=False,
            comment='Evaluation quality flag',
        ),
        sa.Column(
            'strategy_used',
            sa.String(64),
            nullable=False,
            comment="Strategy identifier, e.g. 'rules_v1'",
        ),
        sa.Column(
            'enrichment_context',
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment='Traffic and weather context used during evaluation',
        ),
        sa.Column(
            'evaluated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
            comment='When this evaluation was produced',
        ),
        sa.CheckConstraint(
            'confidence >= 0.0 AND confidence <= 1.0',
            name='ck_eval_confidence',
        ),
    )

    # Indexes
    op.create_index('idx_eval_report_id', 'disaster_evaluations', ['report_id'])
    op.create_index('idx_eval_flag', 'disaster_evaluations', ['flag'])
    op.create_index('idx_eval_evaluated_at', 'disaster_evaluations', ['evaluated_at'])
    op.create_index(op.f('ix_disaster_evaluations_id'), 'disaster_evaluations', ['id'], unique=True)
    op.create_index(op.f('ix_disaster_evaluations_report_id'), 'disaster_evaluations', ['report_id'])


def downgrade():
    op.drop_index(op.f('ix_disaster_evaluations_report_id'), table_name='disaster_evaluations')
    op.drop_index(op.f('ix_disaster_evaluations_id'), table_name='disaster_evaluations')
    op.drop_index('idx_eval_evaluated_at', table_name='disaster_evaluations')
    op.drop_index('idx_eval_flag', table_name='disaster_evaluations')
    op.drop_index('idx_eval_report_id', table_name='disaster_evaluations')
    op.drop_table('disaster_evaluations')

    sa.Enum(name='evaluation_flag').drop(op.get_bind(), checkfirst=True)
