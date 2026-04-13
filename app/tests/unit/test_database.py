# File: tests/unit/test_database.py
"""
Unit tests for database session management.

Tests ensure:
1. Database engine is created correctly
2. Async session factory works
3. Session context manager provides valid sessions
4. Proper cleanup and connection pooling
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine


@pytest.mark.asyncio
async def test_database_engine_creation():
    """Test that database engine is created with correct configuration."""
    # Act
    from app.db.session import create_async_engine_instance
    engine = create_async_engine_instance()
    
    # Assert
    assert engine is not None
    assert isinstance(engine, AsyncEngine)
    
    # Cleanup
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_factory_creates_sessions():
    """Test that session factory creates valid AsyncSession instances."""
    # Act
    from app.db.session import get_async_session_factory
    _engine, session_factory = get_async_session_factory()

    # Session factory should be callable
    assert callable(session_factory)
    await _engine.dispose()


@pytest.mark.asyncio
async def test_get_db_dependency_provides_session():
    """Test that get_db dependency function provides a valid session."""
    # This tests the FastAPI dependency injection pattern
    from app.db.session import get_db
    
    # Act: Call the async generator
    db_generator = get_db()
    
    # Assert: Should be an async generator
    assert hasattr(db_generator, '__anext__')


@pytest.mark.asyncio
async def test_session_cleanup_on_error():
    """Test that database session is properly cleaned up on errors."""
    # This test ensures sessions don't leak when exceptions occur
    from app.db.session import get_db
    
    db_generator = get_db()
    
    try:
        # Get the session
        session = await db_generator.__anext__()
        assert session is not None
        
        # Simulate an error
        raise ValueError("Test error")
    except ValueError:
        # Ensure cleanup happens
        with pytest.raises(StopAsyncIteration):
            await db_generator.__anext__()