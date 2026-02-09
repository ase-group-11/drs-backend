# File: alembic/env.py
"""
Alembic environment configuration (Synchronous version for Windows).

This version uses synchronous SQLAlchemy connections
which work more reliably on Windows and properly handles Azure SSL.
"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Import app settings and models
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.config import settings
from app.db.models.base import Base

# Import all models so Alembic can detect them
from app.db.models.user import User
from app.db.models.emergency_team import EmergencyTeam
from app.db.models.disaster_report import DisasterReport

# Alembic Config object
config = context.config

# Convert async DATABASE_URL to sync version for Alembic
database_url = settings.DATABASE_URL

if database_url.startswith("postgresql+asyncpg://"):
    # Replace asyncpg with psycopg2 for sync connections
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    
    # Fix SSL parameter: asyncpg uses ?ssl=require, psycopg2 uses ?sslmode=require
    if "?ssl=require" in database_url:
        database_url = database_url.replace("?ssl=require", "?sslmode=require")
    elif "&ssl=require" in database_url:
        database_url = database_url.replace("&ssl=require", "&sslmode=require")
    
    print(f"✅ Converted async URL to sync URL for migration")
    print(f"   Using SSL mode: require")

config.set_main_option("sqlalchemy.url", database_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode (synchronous).

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()