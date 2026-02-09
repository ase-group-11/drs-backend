# File: app/tests/unit/test_registration_cache.py
"""
Unit tests for registration cache service.

Tests ensure:
1. Registration data is stored correctly in Redis
2. Registration data can be retrieved
3. Registration data expires after TTL
4. Registration data can be deleted
5. Update operations work correctly
"""

import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.asyncio
async def test_store_registration_data():
    """Test storing registration data in Redis."""
    from app.services.registration_cache import store_registration_data
    
    phone_number = "+1234567890"
    full_name = "John Doe"
    email = "john@example.com"
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_redis.return_value = mock_client
        
        # Act
        await store_registration_data(phone_number, full_name, email)
        
        # Assert
        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        
        # Check key format
        assert call_args[0][0] == f"reg_cache:{phone_number}"
        
        # Check TTL (10 minutes = 600 seconds by default)
        assert call_args[0][1] == 600
        
        # Check data is JSON
        stored_data = json.loads(call_args[0][2])
        assert stored_data["phone_number"] == phone_number
        assert stored_data["full_name"] == full_name
        assert stored_data["email"] == email


@pytest.mark.asyncio
async def test_store_registration_data_without_email():
    """Test storing registration data without email."""
    from app.services.registration_cache import store_registration_data
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_redis.return_value = mock_client
        
        # Act
        await store_registration_data("+1234567890", "John Doe", None)
        
        # Assert
        call_args = mock_client.setex.call_args
        stored_data = json.loads(call_args[0][2])
        assert stored_data["email"] is None


@pytest.mark.asyncio
async def test_get_registration_data():
    """Test retrieving registration data from Redis."""
    from app.services.registration_cache import get_registration_data
    
    phone_number = "+1234567890"
    cached_data = {
        "phone_number": phone_number,
        "full_name": "John Doe",
        "email": "john@example.com"
    }
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = json.dumps(cached_data)
        mock_redis.return_value = mock_client
        
        # Act
        result = await get_registration_data(phone_number)
        
        # Assert
        assert result is not None
        assert result["phone_number"] == phone_number
        assert result["full_name"] == "John Doe"
        assert result["email"] == "john@example.com"


@pytest.mark.asyncio
async def test_get_registration_data_not_found():
    """Test retrieving non-existent registration data."""
    from app.services.registration_cache import get_registration_data
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = None  # Not found
        mock_redis.return_value = mock_client
        
        # Act
        result = await get_registration_data("+1234567890")
        
        # Assert
        assert result is None


@pytest.mark.asyncio
async def test_get_registration_data_invalid_json():
    """Test retrieving corrupted registration data."""
    from app.services.registration_cache import get_registration_data
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = "invalid-json-data"
        mock_redis.return_value = mock_client
        
        # Act
        result = await get_registration_data("+1234567890")
        
        # Assert
        assert result is None  # Should handle JSON decode error


@pytest.mark.asyncio
async def test_delete_registration_data():
    """Test deleting registration data."""
    from app.services.registration_cache import delete_registration_data
    
    phone_number = "+1234567890"
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_redis.return_value = mock_client
        
        # Act
        result = await delete_registration_data(phone_number)
        
        # Assert
        assert result is True
        mock_client.delete.assert_called_once_with(f"reg_cache:{phone_number}")


@pytest.mark.asyncio
async def test_update_registration_data():
    """Test updating existing registration data."""
    from app.services.registration_cache import (
        update_registration_data,
        get_registration_data,
        store_registration_data
    )
    
    phone_number = "+1234567890"
    existing_data = {
        "phone_number": phone_number,
        "full_name": "John Doe",
        "email": "john@example.com"
    }
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = json.dumps(existing_data)
        mock_redis.return_value = mock_client
        
        # Act - Update full name
        with patch('app.services.registration_cache.store_registration_data') as mock_store:
            result = await update_registration_data(
                phone_number,
                full_name="Jane Doe"
            )
            
            # Assert
            assert result is True
            mock_store.assert_called_once()
            call_args = mock_store.call_args
            assert call_args[1]["full_name"] == "Jane Doe"
            assert call_args[1]["email"] == "john@example.com"  # Unchanged


@pytest.mark.asyncio
async def test_update_registration_data_not_found():
    """Test updating non-existent registration data."""
    from app.services.registration_cache import update_registration_data
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        mock_redis.return_value = mock_client
        
        # Act
        result = await update_registration_data(
            "+1234567890",
            full_name="Jane Doe"
        )
        
        # Assert
        assert result is False


@pytest.mark.asyncio
async def test_registration_cache_key_format():
    """Test that cache keys are formatted correctly."""
    from app.services.registration_cache import _get_registration_cache_key
    
    phone_number = "+1234567890"
    
    # Act
    key = _get_registration_cache_key(phone_number)
    
    # Assert
    assert key == f"reg_cache:{phone_number}"
    assert key.startswith("reg_cache:")


@pytest.mark.asyncio
async def test_store_with_custom_expiry():
    """Test storing data with custom expiry time."""
    from app.services.registration_cache import store_registration_data
    
    with patch('app.services.registration_cache.get_redis_client') as mock_redis:
        mock_client = AsyncMock()
        mock_redis.return_value = mock_client
        
        # Act - Store with 5 minute expiry
        await store_registration_data(
            "+1234567890",
            "John Doe",
            expiry_seconds=300
        )
        
        # Assert
        call_args = mock_client.setex.call_args
        assert call_args[0][1] == 300  # 5 minutes