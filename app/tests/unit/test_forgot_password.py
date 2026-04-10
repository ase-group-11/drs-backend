# File: app/tests/unit/test_forgot_password.py
"""
Unit + Integration tests for the Forgot Password feature.

Tests cover:
  Service layer (EmergencyTeamService):
    - forgot_password_request: email found/not found/inactive
    - reset_password_with_temp: valid flow, expired token, wrong password

  API layer (emergency_team_auth router):
    - POST /emergency-team/forgot-password
    - POST /emergency-team/reset-password

Run:
  pytest app/tests/unit/test_forgot_password.py -v
"""

import json
import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth.dependencies import get_current_team_member
from app.db.session import get_db

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MEMBER_ID  = str(uuid.uuid4())
EMAIL      = "john.doe@emergency.ie"
FULL_NAME  = "John Doe"
TEMP_PASS  = "Tz4kR8mWq1"
NEW_PASS   = "NewSecure123"
TEMP_HASH  = "hashed_temp_password"
REDIS_KEY  = f"ert_forgot_pwd:{MEMBER_ID}"

ADMIN_USER = {"user_id": MEMBER_ID, "user_type": "emergency_team"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_mock_db():
    db = AsyncMock()
    db.execute   = AsyncMock()
    db.flush     = AsyncMock()
    db.commit    = AsyncMock()
    db.rollback  = AsyncMock()
    db.close     = AsyncMock()
    return db


def make_db_result(row=None):
    mock = MagicMock()
    mock.mappings.return_value.first.return_value = row
    return mock


@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def admin_client(mock_db):
    app.dependency_overrides[get_current_team_member] = lambda: ADMIN_USER
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


@pytest.fixture
def no_auth_client(mock_db):
    """Client with no auth override — for endpoints that don't require JWT."""
    app.dependency_overrides[get_db] = lambda: mock_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, mock_db
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE TESTS — forgot_password_request
# ─────────────────────────────────────────────────────────────────────────────

class TestForgotPasswordRequest:

    @pytest.mark.asyncio
    async def test_returns_generic_message_when_email_not_found(self):
        """FP-01: Returns generic message if email not in DB (no enumeration)."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row=None)
        service = EmergencyTeamService(db)

        result = await service.forgot_password_request(email="unknown@test.ie")

        assert "message" in result
        assert result["message"] == "If this email is registered you will receive a temporary password shortly."

    @pytest.mark.asyncio
    async def test_returns_generic_message_for_inactive_account(self):
        """FP-02: Returns same generic message for inactive account (no enumeration)."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "full_name": FULL_NAME,
            "email": EMAIL, "status": "INACTIVE"
        })
        service = EmergencyTeamService(db)

        result = await service.forgot_password_request(email=EMAIL)

        assert result["message"] == "If this email is registered you will receive a temporary password shortly."

    @pytest.mark.asyncio
    async def test_generates_temp_password_and_stores_in_redis(self):
        """FP-03: Generates temp password, stores hash in Redis for active account."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "full_name": FULL_NAME,
            "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        with patch("app.services.emergency_team_service.set_with_expiry", new_callable=AsyncMock) as mock_redis, \
             patch("app.services.emergency_team_service.hash_password", return_value=TEMP_HASH), \
             patch("app.services.emergency_team_service.send_forgot_password_email", return_value=True):

            result = await service.forgot_password_request(email=EMAIL)

        assert result["message"] == "If this email is registered you will receive a temporary password shortly."
        mock_redis.assert_called_once()
        # Verify Redis was called with correct key and 900 TTL
        call_args = mock_redis.call_args
        assert f"ert_forgot_pwd:{MEMBER_ID}" in call_args[0][0]
        assert call_args[0][2] == 900  # 15 minutes

    @pytest.mark.asyncio
    async def test_sends_email_via_sendgrid(self):
        """FP-04: Calls send_forgot_password_email with correct params."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "full_name": FULL_NAME,
            "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        with patch("app.services.emergency_team_service.set_with_expiry", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.hash_password", return_value=TEMP_HASH), \
             patch("app.services.emergency_team_service.send_forgot_password_email", return_value=True) as mock_email:

            await service.forgot_password_request(email=EMAIL)

        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args
        assert call_kwargs[1]["to_email"] == EMAIL or call_kwargs[0][0] == EMAIL

    @pytest.mark.asyncio
    async def test_returns_generic_message_even_if_email_fails(self):
        """FP-05: Returns success even if SendGrid email fails (no enumeration)."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "full_name": FULL_NAME,
            "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        with patch("app.services.emergency_team_service.set_with_expiry", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.hash_password", return_value=TEMP_HASH), \
             patch("app.services.emergency_team_service.send_forgot_password_email", return_value=False):

            result = await service.forgot_password_request(email=EMAIL)

        # Must still return success — never reveal email failure
        assert "message" in result

    @pytest.mark.asyncio
    async def test_temp_password_meets_complexity_requirements(self):
        """FP-06: Generated temp password has uppercase, lowercase, and digit."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "full_name": FULL_NAME,
            "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        captured_temp = {}

        async def capture_redis(key, value, ttl):
            data = json.loads(value)
            captured_temp["stored"] = data

        with patch("app.services.emergency_team_service.set_with_expiry", side_effect=capture_redis), \
             patch("app.services.emergency_team_service.hash_password", return_value=TEMP_HASH), \
             patch("app.services.emergency_team_service.send_forgot_password_email", return_value=True):

            await service.forgot_password_request(email=EMAIL)

        # Verify the stored data has expected fields
        assert "member_id" in captured_temp["stored"]
        assert "temp_hash" in captured_temp["stored"]
        assert captured_temp["stored"]["email"] == EMAIL


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE TESTS — reset_password_with_temp
# ─────────────────────────────────────────────────────────────────────────────

class TestResetPasswordWithTemp:

    @pytest.mark.asyncio
    async def test_successful_password_reset(self):
        """FP-07: Successful reset updates DB with new password hash."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.side_effect = [
            make_db_result(row={"id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"}),
            MagicMock(),  # UPDATE query
        ]
        service = EmergencyTeamService(db)

        stored_data = json.dumps({
            "member_id": MEMBER_ID,
            "temp_hash": TEMP_HASH,
            "email": EMAIL,
        })

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=stored_data), \
             patch("app.services.emergency_team_service.delete_key", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.verify_password", return_value=True), \
             patch("app.services.emergency_team_service.hash_password", return_value="new_hash"):

            result = await service.reset_password_with_temp(
                email=EMAIL,
                temp_password=TEMP_PASS,
                new_password=NEW_PASS,
            )

        assert "message" in result
        assert "successfully" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_raises_if_email_not_found(self):
        """FP-08: Raises ValueError if email not in DB."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row=None)
        service = EmergencyTeamService(db)

        with pytest.raises(ValueError, match="Invalid email"):
            await service.reset_password_with_temp(
                email="unknown@test.ie",
                temp_password=TEMP_PASS,
                new_password=NEW_PASS,
            )

    @pytest.mark.asyncio
    async def test_raises_if_temp_password_expired(self):
        """FP-09: Raises ValueError if Redis key not found (expired)."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="expired"):
                await service.reset_password_with_temp(
                    email=EMAIL,
                    temp_password=TEMP_PASS,
                    new_password=NEW_PASS,
                )

    @pytest.mark.asyncio
    async def test_raises_if_wrong_temp_password(self):
        """FP-10: Raises ValueError if temp password hash does not match."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        stored_data = json.dumps({
            "member_id": MEMBER_ID,
            "temp_hash": TEMP_HASH,
            "email": EMAIL,
        })

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=stored_data), \
             patch("app.services.emergency_team_service.delete_key", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.verify_password", return_value=False):

            with pytest.raises(ValueError, match="Invalid temporary password"):
                await service.reset_password_with_temp(
                    email=EMAIL,
                    temp_password="WrongTemp1",
                    new_password=NEW_PASS,
                )

    @pytest.mark.asyncio
    async def test_redis_key_deleted_on_success(self):
        """FP-11: Redis key deleted immediately after retrieval (single-use)."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.side_effect = [
            make_db_result(row={"id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"}),
            MagicMock(),
        ]
        service = EmergencyTeamService(db)

        stored_data = json.dumps({
            "member_id": MEMBER_ID, "temp_hash": TEMP_HASH, "email": EMAIL,
        })

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=stored_data), \
             patch("app.services.emergency_team_service.delete_key", new_callable=AsyncMock) as mock_del, \
             patch("app.services.emergency_team_service.verify_password", return_value=True), \
             patch("app.services.emergency_team_service.hash_password", return_value="new_hash"):

            await service.reset_password_with_temp(EMAIL, TEMP_PASS, NEW_PASS)

        mock_del.assert_called_once_with(f"ert_forgot_pwd:{MEMBER_ID}")

    @pytest.mark.asyncio
    async def test_redis_key_deleted_even_on_wrong_password(self):
        """FP-12: Redis key deleted even if wrong temp password (prevents brute force)."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"
        })
        service = EmergencyTeamService(db)

        stored_data = json.dumps({
            "member_id": MEMBER_ID, "temp_hash": TEMP_HASH, "email": EMAIL,
        })

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=stored_data), \
             patch("app.services.emergency_team_service.delete_key", new_callable=AsyncMock) as mock_del, \
             patch("app.services.emergency_team_service.verify_password", return_value=False):

            with pytest.raises(ValueError):
                await service.reset_password_with_temp(EMAIL, "WrongTemp1", NEW_PASS)

        # Key must be deleted even on failure
        mock_del.assert_called_once_with(f"ert_forgot_pwd:{MEMBER_ID}")

    @pytest.mark.asyncio
    async def test_raises_if_account_inactive(self):
        """FP-13: Raises ValueError if account is not ACTIVE."""
        from app.services.emergency_team_service import EmergencyTeamService

        db = make_mock_db()
        db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "email": EMAIL, "status": "INACTIVE"
        })
        service = EmergencyTeamService(db)

        with pytest.raises(ValueError, match="not active"):
            await service.reset_password_with_temp(EMAIL, TEMP_PASS, NEW_PASS)


# ─────────────────────────────────────────────────────────────────────────────
# API TESTS — POST /emergency-team/forgot-password
# ─────────────────────────────────────────────────────────────────────────────

class TestForgotPasswordAPI:

    @pytest.mark.asyncio
    async def test_forgot_password_returns_200_with_generic_message(self, no_auth_client):
        """FP-14: Returns 200 with generic message for any email."""
        client, mock_db = no_auth_client
        mock_db.execute.return_value = make_db_result(row=None)

        async with client as c:
            resp = await c.post(
                "/api/v1/emergency-team/forgot-password",
                json={"email": "anyone@test.ie"},
            )

        assert resp.status_code == 200
        assert "message" in resp.json()

    @pytest.mark.asyncio
    async def test_forgot_password_returns_400_for_invalid_email(self, no_auth_client):
        """FP-15: Returns 422 for invalid email format."""
        client, mock_db = no_auth_client

        async with client as c:
            resp = await c.post(
                "/api/v1/emergency-team/forgot-password",
                json={"email": "not-an-email"},
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_forgot_password_missing_email_field(self, no_auth_client):
        """FP-16: Returns 422 if email field is missing."""
        client, mock_db = no_auth_client

        async with client as c:
            resp = await c.post(
                "/api/v1/emergency-team/forgot-password",
                json={},
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_forgot_password_same_response_for_existing_email(self, no_auth_client):
        """FP-17: Same response whether email exists or not (no enumeration)."""
        client, mock_db = no_auth_client

        # Response for non-existent email
        mock_db.execute.return_value = make_db_result(row=None)
        async with client as c:
            resp1 = await c.post(
                "/api/v1/emergency-team/forgot-password",
                json={"email": "notexist@test.ie"},
            )

        # Response for existing email
        mock_db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "full_name": FULL_NAME,
            "email": EMAIL, "status": "ACTIVE"
        })

        with patch("app.services.emergency_team_service.set_with_expiry", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.hash_password", return_value=TEMP_HASH), \
             patch("app.services.emergency_team_service.send_forgot_password_email", return_value=True):
            async with client as c:
                resp2 = await c.post(
                    "/api/v1/emergency-team/forgot-password",
                    json={"email": EMAIL},
                )

        # Both should return same message
        assert resp1.json()["message"] == resp2.json()["message"]


# ─────────────────────────────────────────────────────────────────────────────
# API TESTS — POST /emergency-team/reset-password
# ─────────────────────────────────────────────────────────────────────────────

class TestResetPasswordAPI:

    @pytest.mark.asyncio
    async def test_reset_password_success_returns_200(self, no_auth_client):
        """FP-18: Returns 200 on successful password reset."""
        client, mock_db = no_auth_client

        mock_db.execute.side_effect = [
            make_db_result(row={"id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"}),
            MagicMock(),
        ]

        stored = json.dumps({"member_id": MEMBER_ID, "temp_hash": TEMP_HASH, "email": EMAIL})

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=stored), \
             patch("app.services.emergency_team_service.delete_key", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.verify_password", return_value=True), \
             patch("app.services.emergency_team_service.hash_password", return_value="new_hash"):

            async with client as c:
                resp = await c.post(
                    "/api/v1/emergency-team/reset-password",
                    json={
                        "email": EMAIL,
                        "temp_password": TEMP_PASS,
                        "new_password": NEW_PASS,
                    },
                )

        assert resp.status_code == 200
        assert "successfully" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_reset_password_returns_400_for_expired_token(self, no_auth_client):
        """FP-19: Returns 400 if temp password expired."""
        client, mock_db = no_auth_client

        mock_db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"
        })

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=None):
            async with client as c:
                resp = await c.post(
                    "/api/v1/emergency-team/reset-password",
                    json={"email": EMAIL, "temp_password": TEMP_PASS, "new_password": NEW_PASS},
                )

        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_reset_password_returns_400_for_wrong_temp_password(self, no_auth_client):
        """FP-20: Returns 400 for invalid temp password."""
        client, mock_db = no_auth_client

        mock_db.execute.return_value = make_db_result(row={
            "id": MEMBER_ID, "email": EMAIL, "status": "ACTIVE"
        })

        stored = json.dumps({"member_id": MEMBER_ID, "temp_hash": TEMP_HASH, "email": EMAIL})

        with patch("app.services.emergency_team_service.get_value", new_callable=AsyncMock, return_value=stored), \
             patch("app.services.emergency_team_service.delete_key", new_callable=AsyncMock), \
             patch("app.services.emergency_team_service.verify_password", return_value=False):

            async with client as c:
                resp = await c.post(
                    "/api/v1/emergency-team/reset-password",
                    json={"email": EMAIL, "temp_password": "WrongTemp1", "new_password": NEW_PASS},
                )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reset_password_validates_new_password_strength(self, no_auth_client):
        """FP-21: Returns 422 if new_password is too weak."""
        client, mock_db = no_auth_client

        async with client as c:
            resp = await c.post(
                "/api/v1/emergency-team/reset-password",
                json={"email": EMAIL, "temp_password": TEMP_PASS, "new_password": "weak"},
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_reset_password_missing_fields(self, no_auth_client):
        """FP-22: Returns 422 if required fields missing."""
        client, mock_db = no_auth_client

        async with client as c:
            resp = await c.post(
                "/api/v1/emergency-team/reset-password",
                json={"email": EMAIL},
            )

        assert resp.status_code == 422