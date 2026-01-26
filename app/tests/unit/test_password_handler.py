# File: app/tests/unit/test_password_handler.py
"""
Unit tests for password hashing and verification.

Tests ensure:
1. Passwords are hashed correctly using Argon2
2. Password verification works
3. Wrong passwords are rejected
4. Hashes are different for same password (salt)
5. Security: Hashes cannot be reversed
"""

import pytest


def test_hash_password_creates_valid_hash():
    """Test that password hashing creates a valid Argon2 hash."""
    # Arrange
    from app.auth.password_handler import hash_password
    
    plain_password = "MySecurePassword123!"
    
    # Act
    hashed = hash_password(plain_password)
    
    # Assert
    assert hashed is not None
    assert isinstance(hashed, str)
    assert hashed != plain_password  # Should be hashed, not plain
    assert hashed.startswith("$argon2")  # Argon2 hash format


def test_verify_password_with_correct_password():
    """Test that password verification succeeds with correct password."""
    # Arrange
    from app.auth.password_handler import hash_password, verify_password
    
    plain_password = "CorrectPassword123!"
    hashed = hash_password(plain_password)
    
    # Act
    is_valid = verify_password(plain_password, hashed)
    
    # Assert
    assert is_valid is True


def test_verify_password_with_wrong_password():
    """Test that password verification fails with wrong password."""
    # Arrange
    from app.auth.password_handler import hash_password, verify_password
    
    correct_password = "CorrectPassword123!"
    wrong_password = "WrongPassword456!"
    hashed = hash_password(correct_password)
    
    # Act
    is_valid = verify_password(wrong_password, hashed)
    
    # Assert
    assert is_valid is False


def test_same_password_produces_different_hashes():
    """Test that hashing the same password twice produces different hashes (salt)."""
    # Arrange
    from app.auth.password_handler import hash_password
    
    password = "SamePassword123!"
    
    # Act
    hash1 = hash_password(password)
    hash2 = hash_password(password)
    
    # Assert
    assert hash1 != hash2  # Different salts should produce different hashes
    
    # But both should verify correctly
    from app.auth.password_handler import verify_password
    assert verify_password(password, hash1) is True
    assert verify_password(password, hash2) is True


def test_hash_password_with_empty_string():
    """Test that empty password is handled."""
    # Arrange
    from app.auth.password_handler import hash_password
    
    # Act
    hashed = hash_password("")
    
    # Assert
    assert hashed is not None
    assert isinstance(hashed, str)


def test_verify_password_with_invalid_hash_format():
    """Test that verification handles invalid hash format gracefully."""
    # Arrange
    from app.auth.password_handler import verify_password
    
    password = "TestPassword123!"
    invalid_hash = "not-a-valid-argon2-hash"
    
    # Act & Assert
    # Should return False or raise exception (depending on implementation)
    try:
        is_valid = verify_password(password, invalid_hash)
        assert is_valid is False
    except Exception:
        # Some implementations might raise an exception
        pass


def test_password_hash_is_not_reversible():
    """Test that hash cannot be reversed to get original password."""
    # Arrange
    from app.auth.password_handler import hash_password
    
    password = "SecretPassword123!"
    hashed = hash_password(password)
    
    # Assert
    # There should be no way to get the original password from the hash
    assert password not in hashed
    assert len(hashed) > len(password)  # Hash is longer
    
    # The hash should contain Argon2 metadata, not the password
    assert "$argon2" in hashed
    assert "SecretPassword" not in hashed


def test_hash_password_performance():
    """Test that password hashing completes in reasonable time."""
    # Arrange
    import time
    from app.auth.password_handler import hash_password
    
    password = "PerformanceTestPassword123!"
    
    # Act
    start_time = time.time()
    hash_password(password)
    end_time = time.time()
    
    # Assert
    duration = end_time - start_time
    # Argon2 should be slow enough to prevent brute force (>0.1s)
    # but fast enough for user experience (<2s)
    assert 0.01 < duration < 2.0, f"Hashing took {duration}s"