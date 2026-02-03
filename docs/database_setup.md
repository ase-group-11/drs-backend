# Database Setup & Migration Guide

## Prerequisites

1. **PostgreSQL with PostGIS** installed and running
2. **Redis** installed and running
3. **Python environment** activated with all requirements installed

---

## Step 1: Install Dependencies

```bash
# Install requirements (alembic is now included)
pip install -r requirements.txt
```

---

## Step 2: Configure Database URL

Ensure your `.env` file or `app/core/config.py` has the correct `DATABASE_URL`:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/drs_backend
```

---

## Step 3: Enable PostGIS Extension

Connect to your PostgreSQL database and enable PostGIS:

```bash
# Connect to PostgreSQL
psql -U username -d drs_backend

# Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

# Verify installation
SELECT PostGIS_version();

# Exit psql
\q
```

---

## Step 4: Initialize Alembic

```bash
# Initialize Alembic (only run once)
alembic init alembic
```

This creates:
- `alembic/` directory with migration scripts
- `alembic.ini` configuration file

---

## Step 5: Configure Alembic

Edit `alembic/env.py` to import your models and use your database URL:

```python
# Add these imports at the top
from app.core.config import settings
from app.core.database import Base
from app.models import User, Disaster  # Import all models

# Update the target_metadata line (around line 21)
target_metadata = Base.metadata

# Update the sqlalchemy.url in the run_migrations_offline() function
def run_migrations_offline() -> None:
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    # ... rest of function

# Update the sqlalchemy.url in the run_migrations_online() function
def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = settings.DATABASE_URL
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # ... rest of function
```

**Alternative**: Edit `alembic.ini` and set the database URL directly:

```ini
# Line ~63
sqlalchemy.url = postgresql://username:password@localhost:5432/drs_backend
```

---

## Step 6: Create Initial Migration

```bash
# Generate migration from models
alembic revision --autogenerate -m "Add role to User and create Disaster model"
```

This creates a new migration file in `alembic/versions/`.

---

## Step 7: Edit Migration (IMPORTANT)

Open the generated migration file in `alembic/versions/` and **add PostGIS extension** at the top of the `upgrade()` function:

```python
def upgrade() -> None:
    # Enable PostGIS extension FIRST
    op.execute('CREATE EXTENSION IF NOT EXISTS postgis')

    # Then the auto-generated table creation code...
    op.create_table('disasters',
        # ... rest of auto-generated code
    )
```

**Why?** Alembic doesn't auto-detect PostGIS extension requirements. The Disaster model uses the Geography type, which requires PostGIS to be enabled first.

---

## Step 8: Review Migration

Before applying, review the generated migration file to ensure:

1. ✅ PostGIS extension is created first
2. ✅ `users` table gets `role` column added (if users table exists)
3. ✅ `disasters` table is created with all columns:
   - disaster_id (PK)
   - location (Geography POINT)
   - location_lat, location_lng (Float)
   - disaster_type, severity, status (Enums)
   - description (Text)
   - reporter_id (FK to users)
   - image_urls (ARRAY)
   - created_at, updated_at
4. ✅ Indexes are created on lat, lng, severity, status, created_at
5. ✅ Foreign key constraint from disasters.reporter_id to users.user_id

---

## Step 9: Apply Migration

```bash
# Apply the migration
alembic upgrade head
```

You should see output like:
```
INFO  [alembic.runtime.migration] Running upgrade  -> abc123, Add role to User and create Disaster model
```

---

## Step 10: Verify Database Schema

```bash
# Connect to PostgreSQL
psql -U username -d drs_backend

# List all tables
\dt

# Describe users table (should have role column)
\d users

# Describe disasters table
\d disasters

# Check PostGIS is enabled
SELECT PostGIS_version();

# Exit
\q
```

Expected tables:
- `users` (modified with role column and relationship)
- `disasters` (new table)
- `alembic_version` (tracks migrations)

---

## Step 11: Verify Enum Types

PostgreSQL creates custom enum types for our enums. Verify they exist:

```sql
-- In psql
SELECT typname FROM pg_type WHERE typtype = 'e';
```

You should see:
- `userrole`
- `disastertype`
- `disasterseverity`
- `disasterstatus`

---

## Common Issues & Solutions

### Issue 1: PostGIS Extension Not Found

**Error**: `ERROR: type "geography" does not exist`

**Solution**:
```sql
-- Connect to database
psql -U username -d drs_backend

-- Install PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- Verify
SELECT PostGIS_version();
```

If PostGIS is not installed on your system:
```bash
# Ubuntu/Debian
sudo apt-get install postgresql-14-postgis-3

# macOS (Homebrew)
brew install postgis

# Restart PostgreSQL
sudo systemctl restart postgresql
```

---

### Issue 2: Alembic Can't Import Models

**Error**: `ModuleNotFoundError: No module named 'app'`

**Solution**: Run alembic from the project root directory where `app/` is visible, or add to `alembic/env.py`:

```python
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

---

### Issue 3: Existing Users Table

**Error**: Migration tries to create users table but it already exists

**Solution**: The migration should detect existing table and only add the `role` column. If it fails:

```sql
-- Manually add role column
ALTER TABLE users ADD COLUMN role VARCHAR(10) DEFAULT 'citizen' NOT NULL;
ALTER TABLE users ADD COLUMN reported_disasters INTEGER[];

-- Mark migration as applied
alembic stamp head
```

---

### Issue 4: Connection Refused

**Error**: `could not connect to server: Connection refused`

**Solution**:
1. Ensure PostgreSQL is running: `sudo systemctl status postgresql`
2. Check DATABASE_URL in your config
3. Verify PostgreSQL is listening: `sudo netstat -tulpn | grep postgres`

---

## Rollback Instructions

If you need to rollback the migration:

```bash
# Rollback one migration
alembic downgrade -1

# Rollback to base (WARNING: drops all tables)
alembic downgrade base

# Check current migration version
alembic current
```

---

## Adding Future Migrations

When you modify models in the future:

```bash
# 1. Make changes to models in app/models/
# 2. Generate migration
alembic revision --autogenerate -m "Description of changes"

# 3. Review the generated file in alembic/versions/
# 4. Apply migration
alembic upgrade head
```

---

## Testing Migrations

Before applying to production, test in a development environment:

```bash
# Create test database
createdb drs_backend_test

# Update DATABASE_URL to test database
export DATABASE_URL=postgresql://user:pass@localhost:5432/drs_backend_test

# Run migrations
alembic upgrade head

# Test the application
pytest

# Drop test database
dropdb drs_backend_test
```

---

## Production Deployment

For production deployments:

1. **Backup** your database first
2. **Review** the migration file carefully
3. **Test** in staging environment
4. **Apply** during maintenance window
5. **Verify** schema changes
6. **Monitor** application logs

```bash
# Backup database
pg_dump -U username drs_backend > backup_$(date +%Y%m%d).sql

# Apply migration
alembic upgrade head

# If issues occur, rollback
alembic downgrade -1

# Restore from backup if needed
psql -U username drs_backend < backup_20260202.sql
```

---

## Summary Checklist

- [ ] PostgreSQL with PostGIS installed
- [ ] Redis running
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Alembic initialized (`alembic init alembic`)
- [ ] `alembic/env.py` configured to import models
- [ ] PostGIS extension enabled in database
- [ ] Migration generated (`alembic revision --autogenerate`)
- [ ] Migration file edited to include PostGIS extension
- [ ] Migration reviewed for correctness
- [ ] Migration applied (`alembic upgrade head`)
- [ ] Database schema verified in psql
- [ ] Application starts without errors
- [ ] Tests pass (`pytest`)

---

## Next Steps

After successful migration:

1. **Run tests**: `pytest app/tests/`
2. **Start application**: `uvicorn main:app --reload`
3. **Test disaster reporting endpoint**: See `docs/api_testing.md`
4. **Review Developer 2 integration**: See `docs/developer2_integration.md`

---

## Support

For migration issues:
- Check Alembic docs: https://alembic.sqlalchemy.org/
- Check PostGIS docs: https://postgis.net/
- Review migration file in `alembic/versions/`
- Check application logs for errors
