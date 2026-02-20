"""disaster and disaster photos

Revision ID: 07265c50239c
Revises: create_disaster_reports
Create Date: 2026-02-14 22:39:53.837990
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '07265c50239c'
down_revision = 'create_disaster_reports'
branch_labels = None
depends_on = None


def upgrade():
    # Create enums if they don't exist (checkfirst=True)
    from sqlalchemy.dialects import postgresql
    
    disaster_type_enum = postgresql.ENUM('FLOOD', 'FIRE', 'EARTHQUAKE', 'HURRICANE', 'TORNADO', 
                                          'TSUNAMI', 'DROUGHT', 'HEATWAVE', 'COLDWAVE', 'STORM', 'OTHER',
                                          name='disaster_type', create_type=False)
    disaster_type_enum.create(op.get_bind(), checkfirst=True)
    
    disaster_severity_enum = postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL',
                                              name='disaster_severity', create_type=False)
    disaster_severity_enum.create(op.get_bind(), checkfirst=True)
    
    disaster_status_enum = postgresql.ENUM('ACTIVE', 'MONITORING', 'RESOLVED', 'ARCHIVED',
                                            name='disaster_status', create_type=False)
    disaster_status_enum.create(op.get_bind(), checkfirst=True)
    
    disaster_report_status_enum = postgresql.ENUM('PENDING', 'VERIFIED', 'REJECTED', 'DUPLICATE',
                                                   name='disaster_report_status', create_type=False)
    disaster_report_status_enum.create(op.get_bind(), checkfirst=True)
    
    user_role_enum = postgresql.ENUM('RESIDENT', 'PRIVILEGED', 'ADMIN',
                                      name='user_role', create_type=False)
    user_role_enum.create(op.get_bind(), checkfirst=True)
    
    # Create disasters table
    op.create_table('disasters',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tracking_id', sa.String(length=50), nullable=False, comment='User-facing tracking ID (e.g., DIS-2026-001234)'),
        sa.Column('type', disaster_type_enum, nullable=False),
        sa.Column('severity', disaster_severity_enum, nullable=False),
        sa.Column('status', disaster_status_enum, nullable=False),
        sa.Column('report_status', disaster_report_status_enum, nullable=False),
        sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, from_text='ST_GeomFromEWKT', name='geometry'), nullable=False, comment='Geographic location (point geometry) using WGS 84'),
        sa.Column('affected_area', sa.String(length=500), nullable=True, comment='Name of affected area/region'),
        sa.Column('description', sa.Text(), nullable=True, comment='Detailed description of the disaster'),
        sa.Column('is_user_reported', sa.Boolean(), nullable=False, comment='True if reported by user, False if from official source'),
        sa.Column('reporter_id', sa.UUID(as_uuid=False), nullable=True, comment='User who reported this disaster'),
        sa.Column('verified_by_id', sa.UUID(as_uuid=False), nullable=True, comment='Emergency team member who verified this report'),
        sa.Column('disaster_metadata', sa.JSON(), nullable=True, comment='Additional metadata (casualties, damage estimates, etc.)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['emergency_teams.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('idx_disasters_active', 'disasters', ['status'], unique=False, postgresql_where=sa.text("status = 'ACTIVE'"))

    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_disasters_location 
        ON disasters USING gist (location)
    """))
    op.create_index('idx_disasters_report_status', 'disasters', ['report_status'], unique=False)
    op.create_index('idx_disasters_reporter', 'disasters', ['reporter_id'], unique=False)
    op.create_index('idx_disasters_status_created', 'disasters', ['status', 'created_at'], unique=False)
    op.create_index('idx_disasters_tracking_id', 'disasters', ['tracking_id'], unique=False)
    op.create_index('idx_disasters_type_severity', 'disasters', ['type', 'severity'], unique=False)
    op.create_index('idx_disasters_user_reported', 'disasters', ['is_user_reported', 'report_status'], unique=False)
    op.create_index(op.f('ix_disasters_id'), 'disasters', ['id'], unique=True)
    op.create_index(op.f('ix_disasters_is_user_reported'), 'disasters', ['is_user_reported'], unique=False)
    op.create_index(op.f('ix_disasters_report_status'), 'disasters', ['report_status'], unique=False)
    op.create_index(op.f('ix_disasters_reporter_id'), 'disasters', ['reporter_id'], unique=False)
    op.create_index(op.f('ix_disasters_severity'), 'disasters', ['severity'], unique=False)
    op.create_index(op.f('ix_disasters_status'), 'disasters', ['status'], unique=False)
    op.create_index(op.f('ix_disasters_tracking_id'), 'disasters', ['tracking_id'], unique=True)
    op.create_index(op.f('ix_disasters_type'), 'disasters', ['type'], unique=False)
    op.create_index(op.f('ix_disasters_verified_by_id'), 'disasters', ['verified_by_id'], unique=False)
    
    # Create disaster_photos table
    op.create_table('disaster_photos',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('disaster_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('image_url', sa.String(length=500), nullable=False, comment='Cloud storage URL for the photo'),
        sa.Column('caption', sa.String(length=500), nullable=True, comment='Optional caption for the photo'),
        sa.Column('file_size', sa.Integer(), nullable=True, comment='File size in bytes'),
        sa.Column('mime_type', sa.String(length=100), nullable=True, comment='MIME type (e.g., image/jpeg, image/png)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['disaster_id'], ['disasters.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_disaster_photos_disaster_id', 'disaster_photos', ['disaster_id'], unique=False)
    op.create_index(op.f('ix_disaster_photos_disaster_id'), 'disaster_photos', ['disaster_id'], unique=False)
    op.create_index(op.f('ix_disaster_photos_id'), 'disaster_photos', ['id'], unique=True)
    
    # ⚠️ REMOVED: op.drop_table('spatial_ref_sys') - This is a PostGIS system table!
    
    # Alter existing tables (from colleague's migration)
    # op.alter_column('disaster_reports', 'severity',
    #            existing_type=postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='severity'),
    #            type_=disaster_severity_enum,
    #            existing_nullable=False)

    op.execute("""
        ALTER TABLE disaster_reports 
        ALTER COLUMN severity TYPE disaster_severity 
        USING severity::text::disaster_severity
    """)
    op.alter_column('disaster_reports', 'people_affected',
               existing_type=sa.INTEGER(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('disaster_reports', 'multiple_casualties',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('disaster_reports', 'structural_damage',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('disaster_reports', 'hazmat_involved',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('disaster_reports', 'road_blocked',
               existing_type=sa.BOOLEAN(),
               server_default=None,
               existing_nullable=False)
    op.alter_column('disaster_reports', 'status',
               existing_type=postgresql.ENUM('SUBMITTED', 'UNDER_REVIEW', 'ASSIGNED', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED', 'REJECTED', name='report_status'),
               server_default=None,
               existing_nullable=False)
    op.alter_column('disaster_reports', 'response_time',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               type_=sa.DateTime(),
               existing_nullable=True)
    op.alter_column('disaster_reports', 'resolved_time',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               type_=sa.DateTime(),
               existing_nullable=True)
    op.drop_index('idx_report_status_severity', table_name='disaster_reports')
    op.drop_index('ix_disaster_reports_severity', table_name='disaster_reports')
    
    op.alter_column('emergency_teams', 'phone_number',
               existing_type=sa.VARCHAR(length=15),
               type_=sa.String(length=20),
               existing_nullable=False)
    op.alter_column('emergency_teams', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=sa.text('now()'),
               existing_nullable=False)
    op.alter_column('emergency_teams', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=sa.text('now()'),
               existing_nullable=False)
    
    op.add_column('users', sa.Column('role', user_role_enum, nullable=False, server_default='RESIDENT'))
    op.alter_column('users', 'phone_number',
               existing_type=sa.VARCHAR(length=15),
               type_=sa.String(length=20),
               existing_nullable=False)
    op.alter_column('users', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=sa.text('now()'),
               existing_nullable=False)
    op.alter_column('users', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=sa.text('now()'),
               existing_nullable=False)
    op.create_index(op.f('ix_users_role'), 'users', ['role'], unique=False)

def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_users_role'), table_name='users')
    op.alter_column('users', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=None,
               existing_nullable=False)
    op.alter_column('users', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=None,
               existing_nullable=False)
    op.alter_column('users', 'phone_number',
               existing_type=sa.String(length=20),
               type_=sa.VARCHAR(length=15),
               existing_nullable=False)
    op.drop_column('users', 'role')
    op.alter_column('emergency_teams', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=None,
               existing_nullable=False)
    op.alter_column('emergency_teams', 'created_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               server_default=None,
               existing_nullable=False)
    op.alter_column('emergency_teams', 'phone_number',
               existing_type=sa.String(length=20),
               type_=sa.VARCHAR(length=15),
               existing_nullable=False)
    op.create_index('ix_disaster_reports_severity', 'disaster_reports', ['severity'], unique=False)
    op.create_index('idx_report_status_severity', 'disaster_reports', ['status', 'severity'], unique=False)
    op.alter_column('disaster_reports', 'resolved_time',
               existing_type=sa.DateTime(),
               type_=postgresql.TIMESTAMP(timezone=True),
               existing_nullable=True)
    op.alter_column('disaster_reports', 'response_time',
               existing_type=sa.DateTime(),
               type_=postgresql.TIMESTAMP(timezone=True),
               existing_nullable=True)
    op.alter_column('disaster_reports', 'status',
               existing_type=postgresql.ENUM('SUBMITTED', 'UNDER_REVIEW', 'ASSIGNED', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED', 'REJECTED', name='report_status'),
               server_default=sa.text("'SUBMITTED'::report_status"),
               existing_nullable=False)
    op.alter_column('disaster_reports', 'road_blocked',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False)
    op.alter_column('disaster_reports', 'hazmat_involved',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False)
    op.alter_column('disaster_reports', 'structural_damage',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False)
    op.alter_column('disaster_reports', 'multiple_casualties',
               existing_type=sa.BOOLEAN(),
               server_default=sa.text('false'),
               existing_nullable=False)
    op.alter_column('disaster_reports', 'people_affected',
               existing_type=sa.INTEGER(),
               server_default=sa.text('0'),
               existing_nullable=False)
    op.alter_column('disaster_reports', 'severity',
               existing_type=sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='disaster_severity'),
               type_=postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='severity'),
               existing_nullable=False)
    op.create_table('spatial_ref_sys',
    sa.Column('srid', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('auth_name', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('auth_srid', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('srtext', sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
    sa.Column('proj4text', sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
    sa.CheckConstraint('srid > 0 AND srid <= 998999', name='spatial_ref_sys_srid_check'),
    sa.PrimaryKeyConstraint('srid', name='spatial_ref_sys_pkey')
    )
    op.drop_index(op.f('ix_disaster_photos_id'), table_name='disaster_photos')
    op.drop_index(op.f('ix_disaster_photos_disaster_id'), table_name='disaster_photos')
    op.drop_index('idx_disaster_photos_disaster_id', table_name='disaster_photos')
    op.drop_table('disaster_photos')
    op.drop_index(op.f('ix_disasters_verified_by_id'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_type'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_tracking_id'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_status'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_severity'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_reporter_id'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_report_status'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_is_user_reported'), table_name='disasters')
    op.drop_index(op.f('ix_disasters_id'), table_name='disasters')
    op.drop_index('idx_disasters_user_reported', table_name='disasters')
    op.drop_index('idx_disasters_type_severity', table_name='disasters')
    op.drop_index('idx_disasters_tracking_id', table_name='disasters')
    op.drop_index('idx_disasters_status_created', table_name='disasters')
    op.drop_index('idx_disasters_reporter', table_name='disasters')
    op.drop_index('idx_disasters_report_status', table_name='disasters')
    op.drop_index('idx_disasters_location', table_name='disasters', postgresql_using='gist')
    op.drop_index('idx_disasters_active', table_name='disasters', postgresql_where=sa.text("status = 'ACTIVE'"))
    op.drop_table('disasters')
    # ### end Alembic commands ###
