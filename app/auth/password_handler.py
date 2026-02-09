# File: app/auth/password_handler.py
"""
Password hashing and verification using Argon2.

Argon2 is the winner of the Password Hashing Competition and is
recommended for password storage. It provides:
- Resistance to GPU cracking attacks
- Resistance to side-channel attacks
- Configurable memory and time costs

Security features:
- Automatic salt generation
- Configurable time and memory costs
- Modern, secure algorithm (better than bcrypt)
"""

from passlib.context import CryptContext


# Password context using Argon2
# Configuration:
# - argon2: Primary hashing algorithm
# - deprecated="auto": Automatically deprecate old hashes
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,  # 64 MB memory cost
    argon2__time_cost=3,         # 3 iterations
    argon2__parallelism=4,       # 4 parallel threads
)


def hash_password(plain_password: str) -> str:
    """
    Hash a plain password using Argon2.
    
    The hash includes:
    - Algorithm identifier ($argon2id$)
    - Configuration parameters (m, t, p)
    - Random salt (automatically generated)
    - Derived key (the actual hash)
    
    Args:
        plain_password: The password to hash
        
    Returns:
        str: Hashed password string in Argon2 format
        
    Example:
        >>> hashed = hash_password("MySecurePassword123")
        >>> print(hashed)
        $argon2id$v=19$m=65536,t=3,p=4$...
        
    Security:
        - Each call generates a unique salt
        - Same password produces different hashes
        - Hash cannot be reversed to get original password
    """
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.
    
    Args:
        plain_password: The password to verify
        hashed_password: The stored hash to verify against
        
    Returns:
        bool: True if password matches, False otherwise
        
    Example:
        >>> hashed = hash_password("MyPassword123")
        >>> verify_password("MyPassword123", hashed)
        True
        >>> verify_password("WrongPassword", hashed)
        False
        
    Security:
        - Constant-time comparison (prevents timing attacks)
        - Handles invalid hash formats gracefully
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        # Invalid hash format or verification error
        return False


def needs_rehash(hashed_password: str) -> bool:
    """
    Check if a password hash needs to be updated.
    
    This is useful when:
    - Algorithm parameters change (e.g., increased time cost)
    - Migrating from old algorithm (e.g., bcrypt to argon2)
    
    Args:
        hashed_password: The stored hash
        
    Returns:
        bool: True if hash should be regenerated, False otherwise
        
    Usage:
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(plain_password)
            db.commit()
    """
    return pwd_context.needs_update(hashed_password)