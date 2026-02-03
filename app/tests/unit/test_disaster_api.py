import pytest
from fastapi import status

from app.models import DisasterType, DisasterSeverity


@pytest.mark.unit
class TestDisasterReporting:
    """Unit tests for disaster reporting endpoint"""

    def test_report_disaster_success(self, client, auth_token):
        """Test successful disaster report creation"""
        disaster_data = {
            "location_lat": 53.3498,
            "location_lng": -6.2603,
            "disaster_type": DisasterType.FIRE.value,
            "severity": DisasterSeverity.HIGH.value,
            "description": "Large fire reported at city center building",
            "image_urls": ["https://example.com/image1.jpg"]
        }

        response = client.post(
            "/api/v1/disasters/report",
            json=disaster_data,
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        assert response.status_code == status.HTTP_201_CREATED

        data = response.json()
        assert data["disaster_id"] is not None
        assert data["disaster_type"] == DisasterType.FIRE.value
        assert data["severity"] == DisasterSeverity.HIGH.value
        assert data["status"] == "pending"
        assert data["ert_notified"] is True  # Citizen reporter triggers notifications

    def test_report_disaster_unauthorized(self, client):
        """Test disaster report without authentication"""
        disaster_data = {
            "location_lat": 53.3498,
            "location_lng": -6.2603,
            "disaster_type": DisasterType.FIRE.value,
            "severity": DisasterSeverity.HIGH.value,
            "description": "Large fire reported at city center building"
        }

        response = client.post(
            "/api/v1/disasters/report",
            json=disaster_data
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_report_disaster_invalid_location(self, client, auth_token):
        """Test disaster report with location outside Dublin bounds"""
        disaster_data = {
            "location_lat": 51.5074,  # London latitude (outside Dublin)
            "location_lng": -0.1278,   # London longitude
            "disaster_type": DisasterType.FIRE.value,
            "severity": DisasterSeverity.HIGH.value,
            "description": "This should fail validation"
        }

        response = client.post(
            "/api/v1/disasters/report",
            json=disaster_data,
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Dublin bounds" in response.json()["detail"][0]["msg"]

    def test_report_disaster_short_description(self, client, auth_token):
        """Test disaster report with too short description"""
        disaster_data = {
            "location_lat": 53.3498,
            "location_lng": -6.2603,
            "disaster_type": DisasterType.FIRE.value,
            "severity": DisasterSeverity.HIGH.value,
            "description": "Fire"  # Too short (min 10 chars)
        }

        response = client.post(
            "/api/v1/disasters/report",
            json=disaster_data,
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_report_disaster_too_many_images(self, client, auth_token):
        """Test disaster report with more than 5 images"""
        disaster_data = {
            "location_lat": 53.3498,
            "location_lng": -6.2603,
            "disaster_type": DisasterType.FIRE.value,
            "severity": DisasterSeverity.HIGH.value,
            "description": "Fire with many images",
            "image_urls": [
                "https://example.com/img1.jpg",
                "https://example.com/img2.jpg",
                "https://example.com/img3.jpg",
                "https://example.com/img4.jpg",
                "https://example.com/img5.jpg",
                "https://example.com/img6.jpg"  # 6th image - exceeds limit
            ]
        }

        response = client.post(
            "/api/v1/disasters/report",
            json=disaster_data,
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_ert_member_no_notification(self, client, test_db):
        """Test that ERT members don't trigger notifications when reporting"""
        # Create ERT user manually
        from app.models import User, UserRole
        import secrets
        from app.core.cache import redis_client

        ert_user = User(
            mobile_number="+919876543299",
            role=UserRole.ERT.value,
            is_verified=True
        )
        test_db.add(ert_user)
        test_db.commit()
        test_db.refresh(ert_user)

        # Create session token
        token = secrets.token_urlsafe(32)
        session_key = f"session:{token}"
        redis_client.setex(session_key, 3600, str(ert_user.user_id))

        disaster_data = {
            "location_lat": 53.3498,
            "location_lng": -6.2603,
            "disaster_type": DisasterType.MEDICAL.value,
            "severity": DisasterSeverity.CRITICAL.value,
            "description": "Medical emergency at scene"
        }

        response = client.post(
            "/api/v1/disasters/report",
            json=disaster_data,
            headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == status.HTTP_201_CREATED

        data = response.json()
        # ERT members don't trigger notifications
        assert data["ert_notified"] is None


@pytest.mark.unit
class TestDisasterValidation:
    """Unit tests for Pydantic schema validation"""

    def test_disaster_type_enum_validation(self):
        """Test disaster type enum accepts valid values"""
        from app.schemas.disaster_schemas import DisasterCreate

        valid_data = {
            "location_lat": 53.3498,
            "location_lng": -6.2603,
            "disaster_type": DisasterType.FLOOD,
            "severity": DisasterSeverity.MEDIUM,
            "description": "Flooding on main street after heavy rain"
        }

        disaster = DisasterCreate(**valid_data)
        assert disaster.disaster_type == DisasterType.FLOOD

    def test_dublin_bounds_validation(self):
        """Test Dublin geographic bounds validation"""
        from app.schemas.disaster_schemas import DisasterCreate
        from pydantic import ValidationError

        # Test latitude below minimum
        with pytest.raises(ValidationError) as exc_info:
            DisasterCreate(
                location_lat=53.1,  # Below 53.2
                location_lng=-6.3,
                disaster_type=DisasterType.FIRE,
                severity=DisasterSeverity.HIGH,
                description="This should fail validation"
            )

        assert "Dublin bounds" in str(exc_info.value)
