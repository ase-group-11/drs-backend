# File: app/db/session.py
"""
Database session management using SQLAlchemy 2.0 async.

Provides:
- Async engine creation
- Async session factory
- FastAPI dependency for database sessions
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

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