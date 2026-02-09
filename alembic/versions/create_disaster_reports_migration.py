"""Create disaster_reports table

Revision ID: create_disaster_reports
Revises: 387605b8a1e4
Create Date: 2026-02-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'create_disaster_reports'
down_revision = '387605b8a1e4'  # Your initial migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Get database connection
    conn = op.get_bind()
    
    # Create disaster_type enum only if it doesn't exist
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'disaster_type'"
    ))
    if not result.fetchone():
        disaster_type_enum = postgresql.ENUM(
            'FIRE', 'FLOOD', 'EARTHQUAKE', 'MEDICAL_EMERGENCY', 'ACCIDENT',
            'CRIME', 'BUILDING_COLLAPSE', 'GAS_LEAK', 'POWER_OUTAGE',
            'WATER_CONTAMINATION', 'LANDSLIDE', 'STORM', 'HAZMAT',
            'EXPLOSION', 'RIOT', 'TERRORIST_ATTACK', 'OTHER',
            name='disaster_type'
        )
        disaster_type_enum.create(conn)
    
    # Create severity enum only if it doesn't exist
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'severity'"
    ))
    if not result.fetchone():
        severity_enum = postgresql.ENUM(
            'LOW', 'MEDIUM', 'HIGH', 'CRITICAL',
            name='severity'
        )
        severity_enum.create(conn)
    
    # Create report_status enum only if it doesn't exist
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'report_status'"
    ))
    if not result.fetchone():
        report_status_enum = postgresql.ENUM(
            'SUBMITTED', 'UNDER_REVIEW', 'ASSIGNED', 'IN_PROGRESS',
            'RESOLVED', 'CANCELLED', 'REJECTED',
            name='report_status'
        )
        report_status_enum.create(conn)
    
    # Create disaster_reports table
    op.create_table(
        'disaster_reports',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        
        # Reporter reference
        sa.Column('user_id', postgresql.UUID(as_uuid=False), nullable=False),
        
        # Location
        sa.Column('location_address', sa.Text(), nullable=False),
        sa.Column('location_latitude', sa.Float(), nullable=True),
        sa.Column('location_longitude', sa.Float(), nullable=True),
        
        # Disaster information
        sa.Column('disaster_type', postgresql.ENUM(
            'FIRE', 'FLOOD', 'EARTHQUAKE', 'MEDICAL_EMERGENCY', 'ACCIDENT',
            'CRIME', 'BUILDING_COLLAPSE', 'GAS_LEAK', 'POWER_OUTAGE',
            'WATER_CONTAMINATION', 'LANDSLIDE', 'STORM', 'HAZMAT',
            'EXPLOSION', 'RIOT', 'TERRORIST_ATTACK', 'OTHER',
            name='disaster_type', create_type=False
        ), nullable=False),
        sa.Column('severity', postgresql.ENUM(
            'LOW', 'MEDIUM', 'HIGH', 'CRITICAL',
            name='severity', create_type=False
        ), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        
        # Media
        sa.Column('media_urls', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        
        # Impact
        sa.Column('people_affected', sa.Integer(), nullable=False, server_default='0'),
        
        # Critical flags
        sa.Column('multiple_casualties', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('structural_damage', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('hazmat_involved', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('road_blocked', sa.Boolean(), nullable=False, server_default='false'),
        
        # Status and assignment
        sa.Column('status', postgresql.ENUM(
            'SUBMITTED', 'UNDER_REVIEW', 'ASSIGNED', 'IN_PROGRESS',
            'RESOLVED', 'CANCELLED', 'REJECTED',
            name='report_status', create_type=False
        ), nullable=False, server_default='SUBMITTED'),
        sa.Column('assigned_to_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('assigned_department', postgresql.ENUM(
            'MEDICAL', 'POLICE', 'FIRE', 'IT', 
            name='department', create_type=False
        ), nullable=True),
        
        # Timeline
        sa.Column('response_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_time', sa.DateTime(timezone=True), nullable=True),
        
        # Resolution
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        
        # Foreign keys
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['emergency_teams.id'], ondelete='SET NULL'),
    )
    
    # Create indexes
    op.create_index('idx_report_status_severity', 'disaster_reports', ['status', 'severity'])
    op.create_index('idx_report_disaster_status', 'disaster_reports', ['disaster_type', 'status'])
    op.create_index('idx_report_user_status', 'disaster_reports', ['user_id', 'status'])
    op.create_index('idx_report_assigned', 'disaster_reports', ['assigned_to_id', 'status'])
    op.create_index('idx_report_dept_status', 'disaster_reports', ['assigned_department', 'status'])
    op.create_index('idx_report_created', 'disaster_reports', ['created_at', 'status'])
    op.create_index('idx_report_location', 'disaster_reports', ['location_latitude', 'location_longitude'])
    
    # Single column indexes
    op.create_index(op.f('ix_disaster_reports_id'), 'disaster_reports', ['id'], unique=True)
    op.create_index(op.f('ix_disaster_reports_user_id'), 'disaster_reports', ['user_id'])
    op.create_index(op.f('ix_disaster_reports_disaster_type'), 'disaster_reports', ['disaster_type'])
    op.create_index(op.f('ix_disaster_reports_severity'), 'disaster_reports', ['severity'])
    op.create_index(op.f('ix_disaster_reports_status'), 'disaster_reports', ['status'])
    op.create_index(op.f('ix_disaster_reports_assigned_to_id'), 'disaster_reports', ['assigned_to_id'])
    op.create_index(op.f('ix_disaster_reports_assigned_department'), 'disaster_reports', ['assigned_department'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index(op.f('ix_disaster_reports_assigned_department'), table_name='disaster_reports')
    op.drop_index(op.f('ix_disaster_reports_assigned_to_id'), table_name='disaster_reports')
    op.drop_index(op.f('ix_disaster_reports_status'), table_name='disaster_reports')
    op.drop_index(op.f('ix_disaster_reports_severity'), table_name='disaster_reports')
    op.drop_index(op.f('ix_disaster_reports_disaster_type'), table_name='disaster_reports')
    op.drop_index(op.f('ix_disaster_reports_user_id'), table_name='disaster_reports')
    op.drop_index(op.f('ix_disaster_reports_id'), table_name='disaster_reports')
    op.drop_index('idx_report_location', table_name='disaster_reports')
    op.drop_index('idx_report_created', table_name='disaster_reports')
    op.drop_index('idx_report_dept_status', table_name='disaster_reports')
    op.drop_index('idx_report_assigned', table_name='disaster_reports')
    op.drop_index('idx_report_user_status', table_name='disaster_reports')
    op.drop_index('idx_report_disaster_status', table_name='disaster_reports')
    op.drop_index('idx_report_status_severity', table_name='disaster_reports')
    
    # Drop table
    op.drop_table('disaster_reports')
    
    # Drop enums only if they exist and are not used by other tables
    conn = op.get_bind()
    
    # Check if report_status is only used by disaster_reports
    result = conn.execute(sa.text("""
        SELECT COUNT(*) FROM information_schema.columns 
        WHERE udt_name = 'report_status' AND table_name != 'disaster_reports'
    """))
    if result.fetchone()[0] == 0:
        sa.Enum(name='report_status').drop(conn, checkfirst=True)
    
    # Check if severity is only used by disaster_reports
    result = conn.execute(sa.text("""
        SELECT COUNT(*) FROM information_schema.columns 
        WHERE udt_name = 'severity' AND table_name != 'disaster_reports'
    """))
    if result.fetchone()[0] == 0:
        sa.Enum(name='severity').drop(conn, checkfirst=True)
    
    # Check if disaster_type is only used by disaster_reports
    result = conn.execute(sa.text("""
        SELECT COUNT(*) FROM information_schema.columns 
        WHERE udt_name = 'disaster_type' AND table_name != 'disaster_reports'
    """))
    if result.fetchone()[0] == 0:
        sa.Enum(name='disaster_type').drop(conn, checkfirst=True)