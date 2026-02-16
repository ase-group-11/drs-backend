"""updated few fields in disaster_report and disaster tables

Revision ID: 3bf1bc535733
Revises: 07265c50239c
Create Date: 2026-02-16 19:24:56.141399
"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography, Geometry
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '3bf1bc535733'
down_revision = '07265c50239c'
branch_labels = None
depends_on = None


def upgrade():
    # ========== PART 1: disaster_photos TABLE ==========
    
    # Add new column
    op.add_column('disaster_photos', 
        sa.Column('disaster_report_id', sa.UUID(as_uuid=False), 
                  nullable=True,  # Temporarily nullable
                  comment='Report this photo belongs to')
    )
    
    # Migrate existing disaster_id to disaster_report_id if needed
    # (This assumes photos should stay with their current references)
    try:
        op.execute("""
            UPDATE disaster_photos 
            SET disaster_report_id = disaster_id
            WHERE disaster_report_id IS NULL
        """)
    except:
        pass
    
    # Make NOT NULL after migration
    op.alter_column('disaster_photos', 'disaster_report_id',
        existing_type=sa.UUID(as_uuid=False),
        nullable=False
    )
    
    # Update comments
    op.alter_column('disaster_photos', 'image_url',
        existing_type=sa.VARCHAR(length=500),
        comment='Cloud storage URL',
        existing_nullable=False
    )
    op.alter_column('disaster_photos', 'caption',
        existing_type=sa.VARCHAR(length=500),
        comment="User's caption",
        existing_nullable=True
    )
    op.alter_column('disaster_photos', 'mime_type',
        existing_type=sa.VARCHAR(length=100),
        comment='MIME type (e.g., image/jpeg)',
        existing_nullable=True
    )
    
    # Drop old indexes
    try:
        op.drop_index('idx_disaster_photos_disaster_id', table_name='disaster_photos')
    except:
        pass
    try:
        op.drop_index('ix_disaster_photos_disaster_id', table_name='disaster_photos')
    except:
        pass
    
    # Create new indexes
    op.create_index('idx_disaster_photos_report', 'disaster_photos', 
                    ['disaster_report_id'], unique=False)
    op.create_index(op.f('ix_disaster_photos_disaster_report_id'), 'disaster_photos', 
                    ['disaster_report_id'], unique=False)
    
    # Drop old foreign key
    try:
        op.drop_constraint('disaster_photos_disaster_id_fkey', 'disaster_photos', 
                          type_='foreignkey')
    except:
        pass
    
    # Create new foreign key
    op.create_foreign_key(None, 'disaster_photos', 'disaster_reports', 
                         ['disaster_report_id'], ['id'], ondelete='CASCADE')
    
    # Drop old column
    try:
        op.drop_column('disaster_photos', 'disaster_id')
    except:
        pass
    
    # ========== PART 2: disaster_reports TABLE ==========
    
    # Step 1: Add location column as NULLABLE
    op.add_column('disaster_reports', 
        sa.Column('location', 
            Geography(
                geometry_type='POINT', 
                srid=4326, 
                spatial_index=True
            ), 
            nullable=True,
            comment='Geographic location (PostGIS point) from user'
        )
    )
    
    # Step 2: Populate location from lat/lon
    try:
        op.execute("""
            UPDATE disaster_reports 
            SET location = ST_SetSRID(
                ST_MakePoint(location_longitude, location_latitude), 
                4326
            )::geography
            WHERE location_latitude IS NOT NULL 
            AND location_longitude IS NOT NULL
        """)
    except Exception as e:
        print(f"Could not migrate lat/lon data: {e}")
        # Set default location for existing records
        op.execute("""
            UPDATE disaster_reports 
            SET location = ST_SetSRID(ST_MakePoint(-6.2603, 53.3498), 4326)::geography
            WHERE location IS NULL
        """)
    
    # Step 3: Make location NOT NULL
    op.alter_column('disaster_reports', 'location',
        existing_type=Geography(geometry_type='POINT', srid=4326),
        nullable=False
    )
    
    # Step 4: Add report_status as NULLABLE first
    op.add_column('disaster_reports', 
        sa.Column('report_status', 
            sa.Enum('PENDING', 'VERIFIED', 'REJECTED', 'DUPLICATE', 
                    name='disaster_report_status'),
            nullable=True,
            comment='Review status: PENDING/VERIFIED/REJECTED/DUPLICATE'
        )
    )
    
    # Populate report_status with default
    op.execute("""
        UPDATE disaster_reports 
        SET report_status = 'PENDING'
        WHERE report_status IS NULL
    """)
    
    # Make report_status NOT NULL
    op.alter_column('disaster_reports', 'report_status',
        existing_type=sa.Enum('PENDING', 'VERIFIED', 'REJECTED', 'DUPLICATE', 
                              name='disaster_report_status'),
        nullable=False
    )
    
    # Add other new columns (all nullable)
    op.add_column('disaster_reports', 
        sa.Column('disaster_id', sa.UUID(as_uuid=False), nullable=True, 
                  comment='Links to verified disaster record (if approved)')
    )
    op.add_column('disaster_reports', 
        sa.Column('reviewed_by_id', sa.UUID(as_uuid=False), nullable=True, 
                  comment='Emergency team member who reviewed this')
    )
    op.add_column('disaster_reports', 
        sa.Column('reviewed_at', sa.DateTime(), nullable=True, 
                  comment='When this report was reviewed')
    )
    op.add_column('disaster_reports', 
        sa.Column('rejection_reason', sa.Text(), nullable=True, 
                  comment='Reason for rejection (if applicable)')
    )
    
    # Update column comments
    op.alter_column('disaster_reports', 'user_id',
        existing_type=sa.UUID(),
        comment='User who submitted this report',
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'disaster_type',
        existing_type=postgresql.ENUM('FIRE', 'FLOOD', 'EARTHQUAKE', 'MEDICAL_EMERGENCY', 
                                      'ACCIDENT', 'CRIME', 'BUILDING_COLLAPSE', 'GAS_LEAK', 
                                      'POWER_OUTAGE', 'WATER_CONTAMINATION', 'LANDSLIDE', 
                                      'STORM', 'HAZMAT', 'EXPLOSION', 'RIOT', 
                                      'TERRORIST_ATTACK', 'OTHER', name='disaster_type'),
        comment='Type of disaster reported by user',
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'severity',
        existing_type=postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 
                                      name='disaster_severity'),
        comment='Severity assessment by user',
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'description',
        existing_type=sa.TEXT(),
        comment="User's description of the disaster",
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'location_address',
        existing_type=sa.TEXT(),
        comment='Human-readable address from user',
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'people_affected',
        existing_type=sa.INTEGER(),
        comment="User's estimate of people affected",
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'multiple_casualties',
        existing_type=sa.BOOLEAN(),
        comment='User reports multiple casualties',
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'structural_damage',
        existing_type=sa.BOOLEAN(),
        comment='User reports structural damage',
        existing_nullable=False
    )
    op.alter_column('disaster_reports', 'road_blocked',
        existing_type=sa.BOOLEAN(),
        comment='User reports blocked roads',
        existing_nullable=False
    )
    
    # Drop old indexes
    try:
        op.drop_index('idx_report_assigned', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('idx_report_created', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('idx_report_dept_status', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('idx_report_disaster_status', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('ix_disaster_reports_assigned_department', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('ix_disaster_reports_assigned_to_id', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('ix_disaster_reports_status', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('idx_report_location', table_name='disaster_reports')
    except:
        pass
    try:
        op.drop_index('idx_report_user_status', table_name='disaster_reports')
    except:
        pass
    
    # Create new indexes
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_report_location 
        ON disaster_reports 
        USING GIST (location)
    """)
    op.create_index('idx_report_user_status', 'disaster_reports', 
                    ['user_id', 'report_status'], unique=False)
    op.create_index('idx_report_disaster', 'disaster_reports', 
                    ['disaster_id'], unique=False)
    op.create_index('idx_report_status_created', 'disaster_reports', 
                    ['report_status', 'created_at'], unique=False)
    op.create_index('idx_report_type_severity', 'disaster_reports', 
                    ['disaster_type', 'severity'], unique=False)
    op.create_index(op.f('ix_disaster_reports_disaster_id'), 'disaster_reports', 
                    ['disaster_id'], unique=False)
    op.create_index(op.f('ix_disaster_reports_report_status'), 'disaster_reports', 
                    ['report_status'], unique=False)
    op.create_index(op.f('ix_disaster_reports_reviewed_by_id'), 'disaster_reports', 
                    ['reviewed_by_id'], unique=False)
    
    # Drop old foreign key
    try:
        op.drop_constraint('disaster_reports_assigned_to_id_fkey', 'disaster_reports', 
                          type_='foreignkey')
    except:
        pass
    
    # Create new foreign keys
    op.create_foreign_key(None, 'disaster_reports', 'disasters', 
                         ['disaster_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(None, 'disaster_reports', 'emergency_teams', 
                         ['reviewed_by_id'], ['id'], ondelete='SET NULL')
    
    # Drop old columns
    try:
        op.drop_column('disaster_reports', 'location_latitude')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'location_longitude')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'media_urls')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'response_time')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'resolved_time')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'assigned_department')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'hazmat_involved')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'assigned_to_id')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'resolution_notes')
    except:
        pass
    try:
        op.drop_column('disaster_reports', 'status')
    except:
        pass
    
    # ========== PART 3: disasters TABLE ==========
    
    # Add new columns (with defaults for NOT NULL columns)
    op.add_column('disasters', 
        sa.Column('disaster_status', 
            sa.Enum('ACTIVE', 'MONITORING', 'RESOLVED', 'ARCHIVED', name='disaster_status'),
            nullable=False,
            server_default='ACTIVE',
            comment='Disaster lifecycle: ACTIVE/MONITORING/RESOLVED/ARCHIVED')
    )
    op.add_column('disasters', 
        sa.Column('location_address', sa.Text(), nullable=True, 
                  comment='Official address')
    )
    op.add_column('disasters', 
        sa.Column('people_affected', sa.Integer(), nullable=False, 
                  server_default='0',
                  comment='Official count of people affected')
    )
    op.add_column('disasters', 
        sa.Column('multiple_casualties', sa.Boolean(), nullable=False, 
                  server_default='false',
                  comment='Official: Multiple casualties confirmed')
    )
    op.add_column('disasters', 
        sa.Column('structural_damage', sa.Boolean(), nullable=False, 
                  server_default='false',
                  comment='Official: Structural damage confirmed')
    )
    op.add_column('disasters', 
        sa.Column('road_blocked', sa.Boolean(), nullable=False, 
                  server_default='false',
                  comment='Official: Road access blocked')
    )
    op.add_column('disasters', 
        sa.Column('assigned_to_id', sa.UUID(as_uuid=False), nullable=True, 
                  comment='Assigned emergency team member')
    )
    op.add_column('disasters', 
        sa.Column('assigned_department', 
            sa.Enum('MEDICAL', 'POLICE', 'FIRE', 'IT', name='department'),
            nullable=True, 
            comment='Department handling this disaster')
    )
    op.add_column('disasters', 
        sa.Column('response_time', sa.DateTime(), nullable=True, 
                  comment='When emergency response started')
    )
    op.add_column('disasters', 
        sa.Column('resolved_time', sa.DateTime(), nullable=True, 
                  comment='When disaster was resolved')
    )
    op.add_column('disasters', 
        sa.Column('resolution_notes', sa.Text(), nullable=True, 
                  comment='Official resolution notes')
    )
    op.add_column('disasters', 
        sa.Column('created_by_id', sa.UUID(as_uuid=False), nullable=True, 
                  comment='Team member who created this disaster record')
    )
    
    # Update column comments and types
    op.alter_column('disasters', 'type',
        existing_type=postgresql.ENUM('FIRE', 'FLOOD', 'EARTHQUAKE', 'MEDICAL_EMERGENCY', 
                                      'ACCIDENT', 'CRIME', 'BUILDING_COLLAPSE', 'GAS_LEAK', 
                                      'POWER_OUTAGE', 'WATER_CONTAMINATION', 'LANDSLIDE', 
                                      'STORM', 'HAZMAT', 'EXPLOSION', 'RIOT', 
                                      'TERRORIST_ATTACK', 'OTHER', name='disaster_type'),
        comment='Official disaster type (verified by team)',
        existing_nullable=False
    )
    op.alter_column('disasters', 'severity',
        existing_type=postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 
                                      name='disaster_severity'),
        comment='Official severity level (verified by team)',
        existing_nullable=False
    )
    op.alter_column('disasters', 'location',
        existing_type=Geometry(geometry_type='POINT', srid=4326, from_text='ST_GeomFromEWKT', 
                              name='geometry', nullable=False, _spatial_index_reflected=True),
        type_=Geography(geometry_type='POINT', srid=4326, from_text='ST_GeogFromText', 
                       name='geography', nullable=False),
        comment='Official geographic location (PostGIS point)',
        existing_nullable=False
    )
    op.alter_column('disasters', 'description',
        existing_type=sa.TEXT(),
        nullable=False,
        comment='Official disaster description (by emergency team)'
    )
    op.alter_column('disasters', 'disaster_metadata',
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        comment='Additional flexible metadata',
        existing_nullable=True
    )
    
    # Drop old indexes
    try:
        op.drop_index('idx_disasters_report_status', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('idx_disasters_reporter', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('idx_disasters_user_reported', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('ix_disasters_is_user_reported', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('ix_disasters_report_status', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('ix_disasters_reporter_id', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('ix_disasters_status', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('ix_disasters_verified_by_id', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('idx_disasters_active', table_name='disasters')
    except:
        pass
    try:
        op.drop_index('idx_disasters_status_created', table_name='disasters')
    except:
        pass
    
    # Create new indexes
    op.execute("""
        CREATE INDEX idx_disasters_active 
        ON disasters (disaster_status) 
        WHERE disaster_status = 'ACTIVE'
    """)
    op.create_index('idx_disasters_status_created', 'disasters', 
                    ['disaster_status', 'created_at'], unique=False)
    op.create_index('idx_disasters_assigned', 'disasters', 
                    ['assigned_to_id', 'disaster_status'], unique=False)
    op.create_index('idx_disasters_dept_status', 'disasters', 
                    ['assigned_department', 'disaster_status'], unique=False)
    op.create_index(op.f('ix_disasters_assigned_department'), 'disasters', 
                    ['assigned_department'], unique=False)
    op.create_index(op.f('ix_disasters_assigned_to_id'), 'disasters', 
                    ['assigned_to_id'], unique=False)
    op.create_index(op.f('ix_disasters_created_by_id'), 'disasters', 
                    ['created_by_id'], unique=False)
    op.create_index(op.f('ix_disasters_disaster_status'), 'disasters', 
                    ['disaster_status'], unique=False)
    
    # Drop old foreign keys
    try:
        op.drop_constraint('disasters_reporter_id_fkey', 'disasters', type_='foreignkey')
    except:
        pass
    try:
        op.drop_constraint('disasters_verified_by_id_fkey', 'disasters', type_='foreignkey')
    except:
        pass
    
    # Create new foreign keys
    op.create_foreign_key(None, 'disasters', 'emergency_teams', 
                         ['created_by_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(None, 'disasters', 'emergency_teams', 
                         ['assigned_to_id'], ['id'], ondelete='SET NULL')
    
    # Drop old columns
    try:
        op.drop_column('disasters', 'is_user_reported')
    except:
        pass
    try:
        op.drop_column('disasters', 'reporter_id')
    except:
        pass
    try:
        op.drop_column('disasters', 'report_status')
    except:
        pass
    try:
        op.drop_column('disasters', 'verified_by_id')
    except:
        pass
    try:
        op.drop_column('disasters', 'status')
    except:
        pass
    
    # ========== PART 4: users TABLE ==========
    
    # Remove server default from role column
    op.alter_column('users', 'role',
        existing_type=postgresql.ENUM('RESIDENT', 'PRIVILEGED', 'ADMIN', name='user_role'),
        server_default=None,
        existing_nullable=False
    )


def downgrade():
    # NOTE: This is a simplified downgrade - you may need to adjust based on your needs
    
    # users
    op.alter_column('users', 'role',
        existing_type=postgresql.ENUM('RESIDENT', 'PRIVILEGED', 'ADMIN', name='user_role'),
        server_default=sa.text("'RESIDENT'::user_role"),
        existing_nullable=False
    )
    
    # disasters - add back old columns
    op.add_column('disasters', sa.Column('status', 
        postgresql.ENUM('ACTIVE', 'MONITORING', 'RESOLVED', 'ARCHIVED', name='disaster_status'),
        nullable=False, server_default='ACTIVE'))
    op.add_column('disasters', sa.Column('verified_by_id', sa.UUID(), nullable=True))
    op.add_column('disasters', sa.Column('report_status', 
        postgresql.ENUM('PENDING', 'VERIFIED', 'REJECTED', 'DUPLICATE', 
                       name='disaster_report_status'),
        nullable=False, server_default='PENDING'))
    op.add_column('disasters', sa.Column('reporter_id', sa.UUID(), nullable=True))
    op.add_column('disasters', sa.Column('is_user_reported', sa.BOOLEAN(), 
                                         nullable=False, server_default='true'))
    
    # Drop new columns from disasters
    op.drop_column('disasters', 'created_by_id')
    op.drop_column('disasters', 'resolution_notes')
    op.drop_column('disasters', 'resolved_time')
    op.drop_column('disasters', 'response_time')
    op.drop_column('disasters', 'assigned_department')
    op.drop_column('disasters', 'assigned_to_id')
    op.drop_column('disasters', 'road_blocked')
    op.drop_column('disasters', 'structural_damage')
    op.drop_column('disasters', 'multiple_casualties')
    op.drop_column('disasters', 'people_affected')
    op.drop_column('disasters', 'location_address')
    op.drop_column('disasters', 'disaster_status')
    
    # disaster_reports - add back old columns with defaults
    op.add_column('disaster_reports', sa.Column('status', 
        postgresql.ENUM('SUBMITTED', 'UNDER_REVIEW', 'ASSIGNED', 'IN_PROGRESS', 
                       'RESOLVED', 'CANCELLED', 'REJECTED', name='report_status'),
        nullable=False, server_default='SUBMITTED'))
    op.add_column('disaster_reports', sa.Column('location_latitude', sa.Float(), nullable=True))
    op.add_column('disaster_reports', sa.Column('location_longitude', sa.Float(), nullable=True))
    op.add_column('disaster_reports', sa.Column('media_urls', postgresql.JSON(), nullable=True))
    
    # Migrate location back to lat/lon
    op.execute("""
        UPDATE disaster_reports 
        SET location_latitude = ST_Y(location::geometry),
            location_longitude = ST_X(location::geometry)
        WHERE location IS NOT NULL
    """)
    
    # Drop new columns from disaster_reports
    op.drop_column('disaster_reports', 'rejection_reason')
    op.drop_column('disaster_reports', 'reviewed_at')
    op.drop_column('disaster_reports', 'reviewed_by_id')
    op.drop_column('disaster_reports', 'disaster_id')
    op.drop_column('disaster_reports', 'report_status')
    op.drop_column('disaster_reports', 'location')
    
    # disaster_photos - revert FK change
    op.add_column('disaster_photos', sa.Column('disaster_id', sa.UUID(), nullable=True))
    op.execute("UPDATE disaster_photos SET disaster_id = disaster_report_id")
    op.alter_column('disaster_photos', 'disaster_id', nullable=False)
    op.drop_column('disaster_photos', 'disaster_report_id')
    
