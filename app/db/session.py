# File: app/db/session.py
"""
Database session management using SQLAlchemy 2.0 async and sync.

Provides:
- Async engine creation
- Async session factory
- Sync engine for disaster reporting (PostGIS compatibility)
- FastAPI dependency for database sessions
"""

from typing import AsyncGenerator, Generator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool, QueuePool

from app.core.config import settings


def create_async_engine_instance() -> AsyncEngine:
    """
    Create SQLAlchemy async engine with appropriate configuration.
    
    Configuration:
    - Uses asyncpg driver for PostgreSQL
    - Connection pooling based on environment
    - Echo SQL queries in development mode
    
    Returns:
        AsyncEngine: Configured SQLAlchemy async engine
    """
    # Use NullPool for testing to avoid connection issues
    # Use AsyncAdaptedQueuePool for development/production with connection limits
    poolclass = NullPool if settings.ENVIRONMENT == "testing" else AsyncAdaptedQueuePool
    
    # Base engine arguments
    engine_kwargs = {
        "url": settings.DATABASE_URL,
        "echo": settings.DEBUG,
        "future": True,
        "poolclass": poolclass,
        "pool_pre_ping": True,
    }
    
    # Only add pool configuration for AsyncAdaptedQueuePool
    if poolclass == AsyncAdaptedQueuePool:
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 10
    
    engine = create_async_engine(**engine_kwargs)
    
    return engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get async session factory for creating database sessions.
    
    Returns:
        async_sessionmaker: Factory for creating AsyncSession instances
    """
    engine = create_async_engine_instance()
    
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,  # Don't expire objects after commit
        autocommit=False,
        autoflush=False,
    )
    
    return session_factory


# Global session factory instance
async_session_factory = get_async_session_factory()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides database sessions.

    Usage in FastAPI route:
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            ...

    Yields:
        AsyncSession: Database session

    Note:
        Automatically handles session cleanup and commit/rollback.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Sync database session for disaster reporting (PostGIS compatibility)
def create_sync_engine_instance():
    """
    Create synchronous SQLAlchemy engine for PostGIS operations.

    Note: Used for disaster reporting which requires PostGIS/psycopg2.
    """
    # Convert async URL to sync URL (postgresql+asyncpg -> postgresql+psycopg2)
    sync_url = settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql+psycopg2://')

    poolclass = NullPool if settings.ENVIRONMENT == "testing" else QueuePool

    engine_kwargs = {
        "url": sync_url,
        "echo": settings.DEBUG,
        "future": True,
        "poolclass": poolclass,
        "pool_pre_ping": True,
    }

    if poolclass == QueuePool:
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 10

    return create_engine(**engine_kwargs)


# Sync session factory
sync_engine = create_sync_engine_instance()
SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)


def get_sync_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency for synchronous database sessions.

    Used for disaster reporting with PostGIS/psycopg2.

    Yields:
        Session: Synchronous database session
    """
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()