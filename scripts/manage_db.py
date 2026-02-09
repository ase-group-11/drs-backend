#!/usr/bin/env python3
"""
Database Management Script

Manages database operations including:
- Creating initial migration
- Applying migrations
- Rolling back migrations
- Checking migration status
- Resetting database
"""

import asyncio
import sys
import subprocess
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
from app.db.models.base import Base
from app.db.models.emergency_team import EmergencyTeam
from app.db.models.user import User
from app.db.models.disaster_report import DisasterReport  # ✅ NEW: Added DisasterReport


async def create_tables():
    """Create all tables in the database."""
    print("Creating database tables...")
    
    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    await engine.dispose()
    print("✅ Tables created successfully!")


async def drop_tables():
    """Drop all tables from the database."""
    print("Dropping all database tables...")
    
    response = input("Are you sure? This will delete ALL data! (yes/no): ")
    if response.lower() != "yes":
        print("❌ Aborted")
        return
    
    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()
    print("✅ Tables dropped successfully!")


async def check_connection():
    """Check database connection."""
    print("Checking database connection...")
    
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        
        async with engine.connect() as conn:
            result = await conn.execute("SELECT 1")
            await result.fetchone()
        
        await engine.dispose()
        print("✅ Database connection successful!")
        print(f"   Connected to: {settings.DATABASE_URL.split('@')[1] if '@' in settings.DATABASE_URL else 'database'}")
        return True
        
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False


def run_alembic_command(command: list):
    """Run alembic command."""
    try:
        result = subprocess.run(
            ["python", "-m", "alembic"] + command,
            capture_output=True,
            text=True,
            check=True
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Command failed: {e}")
        print(e.stdout)
        print(e.stderr)
        return False


def create_migration(message: str):
    """Create a new migration."""
    print(f"Creating migration: {message}")
    return run_alembic_command(["revision", "--autogenerate", "-m", message])


def apply_migrations():
    """Apply all pending migrations."""
    print("Applying migrations...")
    return run_alembic_command(["upgrade", "head"])


def rollback_migration(steps: int = 1):
    """Rollback migrations."""
    print(f"Rolling back {steps} migration(s)...")
    return run_alembic_command(["downgrade", f"-{steps}"])


def show_current():
    """Show current migration."""
    print("Current migration status:")
    return run_alembic_command(["current"])


def show_history():
    """Show migration history."""
    print("Migration history:")
    return run_alembic_command(["history"])


async def reset_database():
    """Reset database (drop all, create all, run migrations)."""
    print("⚠️  Resetting database...")
    
    response = input("This will DELETE ALL DATA and recreate tables. Continue? (yes/no): ")
    if response.lower() != "yes":
        print("❌ Aborted")
        return
    
    # Drop all tables
    await drop_tables()
    
    # Run migrations
    print("\n📝 Applying migrations...")
    apply_migrations()
    
    print("\n✅ Database reset complete!")


def print_help():
    """Print help message."""
    help_text = f"""
Database Management Tool

Usage: python manage_db.py <command>

Commands:
  check              Check database connection
  create             Create all tables (without migrations)
  drop               Drop all tables
  migrate MESSAGE    Create new migration
  upgrade            Apply all pending migrations
  downgrade [N]      Rollback N migrations (default: 1)
  current            Show current migration
  history            Show migration history
  reset              Reset database (drop + create + migrate)
  help               Show this help message

Examples:
  python manage_db.py check
  python manage_db.py migrate "Add user table"
  python manage_db.py upgrade
  python manage_db.py downgrade 1
  python manage_db.py reset

Environment:
  Database: {settings.DATABASE_URL.split('@')[1] if '@' in settings.DATABASE_URL else 'Not configured'}
  Environment: {settings.ENVIRONMENT}
"""
    print(help_text)


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "check":
        await check_connection()
    
    elif command == "create":
        await create_tables()
    
    elif command == "drop":
        await drop_tables()
    
    elif command == "migrate":
        if len(sys.argv) < 3:
            print("❌ Please provide migration message")
            print('Example: python manage_db.py migrate "Add user table"')
            return
        message = " ".join(sys.argv[2:])
        create_migration(message)
    
    elif command == "upgrade":
        apply_migrations()
    
    elif command == "downgrade":
        steps = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        rollback_migration(steps)
    
    elif command == "current":
        show_current()
    
    elif command == "history":
        show_history()
    
    elif command == "reset":
        await reset_database()
    
    elif command == "help":
        print_help()
    
    else:
        print(f"❌ Unknown command: {command}")
        print_help()


if __name__ == "__main__":
    asyncio.run(main())