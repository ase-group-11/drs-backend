"""Replace with disaster_chat_sessions table

Revision ID: replace_chat_table_002
Revises: 224596e82083
Create Date: 2026-04-08 00:00:00.000000

Creates disaster_chat_sessions table (chunk-based bulk insert).
Each row = one chunk of up to 50 messages stored as JSONB array.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision      = 'replace_chat_table_002'
down_revision = '224596e82083'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # Drop old table if it exists from any previous migration
    op.execute("DROP TABLE IF EXISTS disaster_chat_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS disaster_chat_sessions CASCADE")

    op.create_table(
        'disaster_chat_sessions',

        # From Base
        sa.Column('id',         sa.String(36),               primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),  server_default=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True),  nullable=True),

        # Chunk fields
        sa.Column('disaster_id',  sa.String(36), nullable=False),
        sa.Column('chunk_number', sa.Integer(),  nullable=False),
        sa.Column('from_seq',     sa.Integer(),  nullable=False),
        sa.Column('to_seq',       sa.Integer(),  nullable=False),
        sa.Column('messages',     JSONB(),        nullable=False, server_default='[]'),
    )

    op.create_index(
        'ix_chat_session_disaster_chunk',
        'disaster_chat_sessions',
        ['disaster_id', 'chunk_number'],
    )


def downgrade() -> None:
    op.drop_index('ix_chat_session_disaster_chunk', table_name='disaster_chat_sessions')
    op.drop_table('disaster_chat_sessions')