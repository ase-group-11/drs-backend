import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.core.database import Base
from app.dependencies import get_db
from main import app

# Test database URL - Use PostgreSQL test database
SQLALCHEMY_TEST_DATABASE_URL = "postgresql://pgadmin:Ase4life!@localhost:5432/drs_backend_test"

@pytest.fixture(scope="function")
def test_db():
    """
    Create a fresh database for each test.
    Uses PostgreSQL to properly test PostGIS features.
    """
    from sqlalchemy import text

    # Create test engine
    engine = create_engine(SQLALCHEMY_TEST_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        # Clean up tables after test
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(test_db):
    """
    FastAPI test client with overridden database dependency.
    """
    def override_get_db():
        try:
            yield test_db
        finally:
            test_db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()

@pytest.fixture
def sample_user(test_db):
    """
    Create a sample user for testing.
    """
    from app.models import User, UserRole

    user = User(
        mobile_number="+919876543210",
        role=UserRole.CITIZEN.value,
        is_verified=True
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    return user

@pytest.fixture
def ert_user(test_db):
    """
    Create a sample ERT user for testing.
    """
    from app.models import User, UserRole

    user = User(
        mobile_number="+919876543211",
        role=UserRole.ERT.value,
        is_verified=True
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    return user

@pytest.fixture
def auth_token(sample_user):
    """
    Generate authentication token for sample user.
    """
    import secrets
    from app.core.cache import redis_client

    token = secrets.token_urlsafe(32)
    session_key = f"session:{token}"
    redis_client.setex(session_key, 3600, str(sample_user.user_id))

    return token
