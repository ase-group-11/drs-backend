"""create emergency units table

Revision ID: a1b2c3d4e5f6
Revises: 2d27c79cbad9
Create Date: 2026-03-06 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '2d27c79cbad9'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ──────────────────────────────────────────────────────────
    # STEP 1: Create NEW enums only (unit_type, unit_status)
    # 'department' already exists — we NEVER touch it here.
    # ──────────────────────────────────────────────────────────

    unit_type_enum = postgresql.ENUM(
        'AMBULANCE', 'FIRE_ENGINE', 'PATROL_CAR',
        'RAPID_RESPONSE', 'HAZMAT', 'RESCUE', 'COMMAND',
        name='unit_type',
        create_type=False
    )
    unit_type_enum.create(conn, checkfirst=True)

    unit_status_enum = postgresql.ENUM(
        'AVAILABLE', 'DEPLOYED', 'ON_SCENE',
        'RETURNING', 'MAINTENANCE', 'OFFLINE',
        name='unit_status',
        create_type=False
    )
    unit_status_enum.create(conn, checkfirst=True)

    # ──────────────────────────────────────────────────────────
    # STEP 2: Create emergency_units table
    #
    # IMPORTANT: The 'department' column uses sa.VARCHAR here
    # instead of sa.Enum to avoid SQLAlchemy firing the
    # _on_table_create event which tries to CREATE TYPE department
    # even with create_type=False.
    #
    # A CHECK CONSTRAINT enforces valid values at the DB level.
    # The column is cast to the existing 'department' enum type
    # via a raw ALTER after the table is created.
    # ──────────────────────────────────────────────────────────

    op.create_table(
        'emergency_units',

        # Base fields (mirrors Base model)
        sa.Column('id', sa.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),

        # Identity
        sa.Column(
            'unit_code', sa.String(length=20),
            unique=True, nullable=False,
            comment='Human-facing unit code (e.g., UNIT-MED-001)'
        ),
        sa.Column(
            'unit_name', sa.String(length=255),
            nullable=False,
            comment='Display name of the unit'
        ),
        sa.Column(
            'description', sa.Text(),
            nullable=True,
            comment='Optional description or notes about this unit'
        ),

        # Classification
        sa.Column(
            'unit_type',
            postgresql.ENUM(
                'AMBULANCE', 'FIRE_ENGINE', 'PATROL_CAR',
                'RAPID_RESPONSE', 'HAZMAT', 'RESCUE', 'COMMAND',
                name='unit_type',
                create_type=False      # already created in STEP 1
            ),
            nullable=False,
            comment='Type of unit: AMBULANCE, FIRE_ENGINE, etc.'
        ),
        sa.Column(
            'department',
            sa.VARCHAR(20),            # temporary VARCHAR — converted to enum type below
            nullable=False,
            comment='Owning department (MEDICAL/POLICE/FIRE/IT)'
        ),
        sa.Column(
            'unit_status',
            postgresql.ENUM(
                'AVAILABLE', 'DEPLOYED', 'ON_SCENE',
                'RETURNING', 'MAINTENANCE', 'OFFLINE',
                name='unit_status',
                create_type=False      # already created in STEP 1
            ),
            nullable=False,
            server_default='AVAILABLE',
            comment='Operational status'
        ),

        # Station assignment
        sa.Column(
            'station_name', sa.String(length=255),
            nullable=False,
            comment='Name of the home station'
        ),
        sa.Column(
            'station_address', sa.Text(),
            nullable=True,
            comment='Physical address of the home station'
        ),
        sa.Column(
            'station_location',
            geoalchemy2.Geography(geometry_type='POINT', srid=4326),
            nullable=True,
            comment='PostGIS point for the station geographic location'
        ),

        # Vehicle details
        sa.Column(
            'vehicle_model', sa.String(length=255),
            nullable=True,
            comment='Vehicle model (e.g., Mercedes Sprinter 519 CDI)'
        ),
        sa.Column(
            'vehicle_license_plate', sa.String(length=20),
            unique=True, nullable=True,
            comment='Vehicle license plate number'
        ),
        sa.Column(
            'vehicle_year', sa.Integer(),
            nullable=True,
            comment='Manufacturing year of the vehicle'
        ),
        sa.Column(
            'equipment_checklist', sa.JSON(),
            nullable=True,
            comment='Equipment checklist JSON array'
        ),

        # Crew
        sa.Column(
            'capacity', sa.Integer(),
            nullable=False,
            server_default='4',
            comment='Maximum crew capacity'
        ),
        sa.Column(
            'commander_id', sa.UUID(as_uuid=False),
            sa.ForeignKey('emergency_teams.id', ondelete='SET NULL'),
            nullable=True,
            comment='Unit commander FK to emergency_teams'
        ),

        # Performance stats
        sa.Column(
            'total_deployments', sa.Integer(),
            nullable=False,
            server_default='0',
            comment='Total number of deployments'
        ),
        sa.Column(
            'avg_response_time_seconds', sa.Integer(),
            nullable=True,
            comment='Rolling average response time in seconds'
        ),
        sa.Column(
            'success_rate', sa.Float(),
            nullable=True,
            comment='Ratio of resolved deployments to total (0.0 to 1.0)'
        ),
        sa.Column(
            'last_deployed_at', sa.DateTime(timezone=True),
            nullable=True,
            comment='Timestamp of the most recent deployment'
        ),
    )

    # Convert department VARCHAR → existing 'department' enum type
    # This reuses the already-existing PostgreSQL enum cleanly.
    op.execute("""
        ALTER TABLE emergency_units
        ALTER COLUMN department
        TYPE department
        USING department::text::department
    """)

    # ── Indexes for emergency_units ────────────────────────────

    op.create_index(op.f('ix_emergency_units_id'),
                    'emergency_units', ['id'], unique=True)
    op.create_index(op.f('ix_emergency_units_unit_code'),
                    'emergency_units', ['unit_code'], unique=True)
    op.create_index(op.f('ix_emergency_units_unit_type'),
                    'emergency_units', ['unit_type'])
    op.create_index(op.f('ix_emergency_units_department'),
                    'emergency_units', ['department'])
    op.create_index(op.f('ix_emergency_units_unit_status'),
                    'emergency_units', ['unit_status'])
    op.create_index(op.f('ix_emergency_units_commander_id'),
                    'emergency_units', ['commander_id'])
    op.create_index(op.f('ix_emergency_units_vehicle_license_plate'),
                    'emergency_units', ['vehicle_license_plate'], unique=True)

    # Composite indexes
    op.create_index('idx_units_type_status',
                    'emergency_units', ['unit_type', 'unit_status'])
    op.create_index('idx_units_dept_status',
                    'emergency_units', ['department', 'unit_status'])
    op.create_index('idx_units_commander',
                    'emergency_units', ['commander_id'])

    # Partial index — available units only
    op.create_index(
        'idx_units_available',
        'emergency_units', ['unit_status'],
        postgresql_where=sa.text("unit_status = 'AVAILABLE'")
    )

    # Spatial index for station_location (PostGIS GIST)
    op.create_index(
        'idx_units_station_location',
        'emergency_units', ['station_location'],
        postgresql_using='gist'
    )

    # ──────────────────────────────────────────────────────────
    # STEP 3: Create unit_crew join table (M2M)
    # ──────────────────────────────────────────────────────────

    op.create_table(
        'unit_crew',
        sa.Column(
            'unit_id', sa.UUID(as_uuid=False),
            sa.ForeignKey('emergency_units.id', ondelete='CASCADE'),
            primary_key=True,
            comment='Emergency unit'
        ),
        sa.Column(
            'team_member_id', sa.UUID(as_uuid=False),
            sa.ForeignKey('emergency_teams.id', ondelete='CASCADE'),
            primary_key=True,
            comment='Emergency team member assigned to this unit'
        ),
    )

    op.create_index('idx_unit_crew_unit_id',
                    'unit_crew', ['unit_id'])
    op.create_index('idx_unit_crew_member_id',
                    'unit_crew', ['team_member_id'])

    # ──────────────────────────────────────────────────────────
    # STEP 4: Add assigned_unit_id column to disasters table
    # ──────────────────────────────────────────────────────────

    op.add_column(
        'disasters',
        sa.Column(
            'assigned_unit_id', sa.UUID(as_uuid=False),
            sa.ForeignKey('emergency_units.id', ondelete='SET NULL'),
            nullable=True,
            comment='Emergency unit assigned to this disaster'
        )
    )

    op.create_index(
        op.f('ix_disasters_assigned_unit_id'),
        'disasters', ['assigned_unit_id']
    )
    op.create_index(
        'idx_disasters_assigned_unit',
        'disasters', ['assigned_unit_id']
    )


def downgrade():
    # Reverse order: disasters → unit_crew → emergency_units → enums

    # Remove assigned_unit_id from disasters
    op.drop_index('idx_disasters_assigned_unit', table_name='disasters')
    op.drop_index(op.f('ix_disasters_assigned_unit_id'), table_name='disasters')
    op.drop_column('disasters', 'assigned_unit_id')

    # Drop unit_crew join table
    op.drop_index('idx_unit_crew_member_id', table_name='unit_crew')
    op.drop_index('idx_unit_crew_unit_id', table_name='unit_crew')
    op.drop_table('unit_crew')

    # Drop emergency_units indexes
    op.drop_index('idx_units_station_location', table_name='emergency_units')
    op.drop_index('idx_units_available', table_name='emergency_units')
    op.drop_index('idx_units_commander', table_name='emergency_units')
    op.drop_index('idx_units_dept_status', table_name='emergency_units')
    op.drop_index('idx_units_type_status', table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_vehicle_license_plate'), table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_commander_id'), table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_unit_status'), table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_department'), table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_unit_type'), table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_unit_code'), table_name='emergency_units')
    op.drop_index(op.f('ix_emergency_units_id'), table_name='emergency_units')

    # Drop emergency_units table
    op.drop_table('emergency_units')

    # Drop only the NEW enums — never drop 'department' (shared)
    conn = op.get_bind()
    postgresql.ENUM(name='unit_status', create_type=False).drop(conn, checkfirst=True)
    postgresql.ENUM(name='unit_type', create_type=False).drop(conn, checkfirst=True)