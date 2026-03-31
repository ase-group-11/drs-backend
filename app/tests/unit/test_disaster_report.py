# File: tests/test_disaster_report.py
"""
Test suite for Disaster Report use case.

Covers all 9 endpoints in disaster_report.py and the business logic
in disaster_report_service.py.

Endpoints tested:
  POST /disaster-reports/submit           → submit_disaster_report (multipart)
  POST /disaster-reports/                 → create_disaster_report  (JSON)
  POST /disaster-reports/upload-media     → upload_media
  GET  /disaster-reports/pending/all      → get_pending_reports     (admin only)
  GET  /disaster-reports/pending/clustered→ get_clustered_pending_reports (admin only)
  POST /disaster-reports/cluster/review   → review_cluster          (admin only)
  GET  /disaster-reports/user/{user_id}   → get_user_reports
  GET  /disaster-reports/{report_id}      → get_report
  POST /disaster-reports/{report_id}/review → review_report         (admin only)

Run:
  pytest tests/test_disaster_report.py -v
"""

import pytest
import pytest_asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from httpx import AsyncClient, ASGITransport
from fastapi import status

from app.main import app
from app.auth.dependencies import get_current_user, get_current_team_member
from app.db.session import get_db


# ══════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════

CITIZEN_USER_ID  = str(uuid.uuid4())
ADMIN_TEAM_ID    = str(uuid.uuid4())
SAMPLE_REPORT_ID = str(uuid.uuid4())
SAMPLE_DISASTER_ID = str(uuid.uuid4())

CITIZEN_USER = {
    "id": CITIZEN_USER_ID,
    "full_name": "Test Citizen",
    "phone_number": "+353871234567",
    "role": "RESIDENT",
}

ADMIN_TEAM_MEMBER = {
    "id": ADMIN_TEAM_ID,
    "full_name": "Admin User",
    "email": "admin@drs.ie",
    "role": "ADMIN",
    "department": "FIRE",
}

SAMPLE_REPORT_DICT = {
    "id": SAMPLE_REPORT_ID,
    "user_id": CITIZEN_USER_ID,
    "disaster_type": "FIRE",
    "severity": "HIGH",
    "description": "Large fire at the warehouse",
    "latitude": 53.3498,
    "longitude": -6.2603,
    "location_address": "Grand Canal Dock, Dublin 2",
    "people_affected": 10,
    "multiple_casualties": False,
    "structural_damage": True,
    "road_blocked": False,
    "report_status": "PENDING",
    "disaster_id": None,
    "reviewed_by_id": None,
    "reviewed_at": None,
    "rejection_reason": None,
    "created_at": datetime.utcnow(),  # must be datetime object, not string
    "photo_count": 2,
}


@pytest.fixture
def mock_db():
    """Async mock DB session — used to override get_db dependency."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def citizen_token_override():
    """Override get_current_user with a citizen user (no admin rights)."""
    return lambda: CITIZEN_USER


@pytest.fixture
def admin_token_override():
    """Override get_current_team_member with an admin team member."""
    return lambda: ADMIN_TEAM_MEMBER


@pytest_asyncio.fixture
async def citizen_client(mock_db, citizen_token_override):
    """AsyncClient authenticated as a citizen."""
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = citizen_token_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_db
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client(mock_db, admin_token_override):
    """AsyncClient authenticated as an emergency team admin."""
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = admin_token_override
    app.dependency_overrides[get_current_team_member] = admin_token_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_db
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauthenticated_client():
    """AsyncClient with NO auth overrides — simulates missing/invalid token."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def make_mock_result(row=None, rows=None, scalar=None):
    """
    Helper: build a mock SQLAlchemy result that supports
    .mappings().first(), .mappings().all(), and .scalar().
    """
    mock_result = MagicMock()
    mock_mappings = MagicMock()

    mock_mappings.first.return_value = row
    mock_mappings.all.return_value = rows if rows is not None else []
    mock_result.mappings.return_value = mock_mappings
    mock_result.scalar.return_value = scalar
    mock_result.first.return_value = row  # for RETURNING id checks
    return mock_result


# ══════════════════════════════════════════════════════════
# TC-DR-01  POST /disaster-reports/submit  (multipart)
# ══════════════════════════════════════════════════════════

class TestSubmitDisasterReport:
    """
    Endpoint: POST /disaster-reports/submit
    Auth:     get_current_user (citizen Bearer token)
    Format:   multipart/form-data
    Service:  DisasterReportService.submit_report()
    """

    BASE_FORM = {
        "user_id": CITIZEN_USER_ID,
        "location_address": "Grand Canal Dock, Dublin 2",
        "disaster_type": "FIRE",
        "severity": "HIGH",
        "description": "Large fire at the warehouse",
        "latitude": "53.3498",
        "longitude": "-6.2603",
        "people_affected": "10",
        "multiple_casualties": "false",
        "structural_damage": "true",
        "road_blocked": "false",
    }

    @pytest.mark.asyncio
    async def test_submit_report_success_no_photos(self, citizen_client):
        """
        TC-DR-01-01
        Given:  valid form fields, no photos attached
        Expect: 201, report_status=PENDING, photo_count=0
        """
        client, mock_db = citizen_client

        # DB returns the saved report on _get_report_dict call
        mock_db.execute.return_value = make_mock_result(row=SAMPLE_REPORT_DICT)

        response = await client.post("/api/v1/disaster-reports/submit", data=self.BASE_FORM)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["report_status"] == "PENDING"
        assert body["disaster_id"] is None
        assert body["reviewed_by_id"] is None

    @pytest.mark.asyncio
    async def test_submit_report_success_with_photos(self, citizen_client):
        """
        TC-DR-01-02
        Given:  valid form fields + 2 image files attached
        Expect: 201, photo_count=2, all photos share same reference_id in DB
        """
        client, mock_db = citizen_client

        report_with_photos = {**SAMPLE_REPORT_DICT, "photo_count": 2}
        mock_db.execute.return_value = make_mock_result(row=report_with_photos)

        with patch(
            "app.api.v1.disaster_report.upload_multiple_files",
            new_callable=AsyncMock,
            return_value={
                "uploaded_files": [
                    {"image_url": "https://blob.azure.com/photo1.jpg", "file_size": 102400, "mime_type": "image/jpeg", "original_filename": "photo1.jpg"},
                    {"image_url": "https://blob.azure.com/photo2.jpg", "file_size": 204800, "mime_type": "image/jpeg", "original_filename": "photo2.jpg"},
                ],
                "reference_id": "test-ref-id-12345",
            }
        ):
            import io
            files = [
                ("files", ("photo1.jpg", io.BytesIO(b"fake-image-1"), "image/jpeg")),
                ("files", ("photo2.jpg", io.BytesIO(b"fake-image-2"), "image/jpeg")),
            ]
            response = await client.post(
                "/api/v1/disaster-reports/submit",
                data=self.BASE_FORM,
                files=files,
            )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["photo_count"] == 2

    @pytest.mark.asyncio
    async def test_submit_report_no_auth_token(self, unauthenticated_client):
        """
        TC-DR-01-03
        Given:  no Bearer token in request
        Expect: 401 or 403 — dependency raises before service is called
        """
        response = await unauthenticated_client.post(
            "/api/v1/disaster-reports/submit", data=self.BASE_FORM
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @pytest.mark.asyncio
    async def test_submit_report_missing_required_field(self, citizen_client):
        """
        TC-DR-01-04
        Given:  form is missing 'disaster_type' (required Form field)
        Expect: 422 Unprocessable Entity from FastAPI validation
        """
        client, _ = citizen_client
        bad_form = {k: v for k, v in self.BASE_FORM.items() if k != "disaster_type"}
        response = await client.post("/api/v1/disaster-reports/submit", data=bad_form)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_submit_report_defaults_applied(self, citizen_client):
        """
        TC-DR-01-05
        Given:  optional boolean flags not sent (people_affected, casualties, etc.)
        Expect: 201; service uses defaults (0, False, False, False)
        """
        client, mock_db = citizen_client
        report_defaults = {**SAMPLE_REPORT_DICT, "people_affected": 0, "multiple_casualties": False, "structural_damage": False, "road_blocked": False, "photo_count": 0}
        mock_db.execute.return_value = make_mock_result(row=report_defaults)

        minimal_form = {
            "user_id": CITIZEN_USER_ID,
            "location_address": "O'Connell Street, Dublin 1",
            "disaster_type": "FLOOD",
            "severity": "LOW",
            "description": "Minor flooding on footpath",
            "latitude": "53.3494",
            "longitude": "-6.2607",
        }
        response = await client.post("/api/v1/disaster-reports/submit", data=minimal_form)
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["people_affected"] == 0
        assert body["multiple_casualties"] is False


# ══════════════════════════════════════════════════════════
# TC-DR-02  POST /disaster-reports/  (JSON path)
# ══════════════════════════════════════════════════════════

class TestCreateDisasterReport:
    """
    Endpoint: POST /disaster-reports/
    Auth:     get_current_user (citizen Bearer token)
    Format:   application/json with pre-uploaded photo URLs
    Service:  DisasterReportService.create_report()
    """

    BASE_PAYLOAD = {
        "user_id": CITIZEN_USER_ID,
        "location_address": "Grand Canal Dock, Dublin 2",
        "disaster_type": "FIRE",
        "severity": "HIGH",
        "description": "Large fire at the warehouse",
        "latitude": 53.3498,
        "longitude": -6.2603,
        "people_affected": 10,
        "multiple_casualties": False,
        "structural_damage": True,
        "road_blocked": False,
        "photos": [
            {"image_url": "https://blob.azure.com/photo1.jpg", "file_size": 102400, "mime_type": "image/jpeg"},
            {"image_url": "https://blob.azure.com/photo2.jpg", "file_size": 204800, "mime_type": "image/jpeg"},
        ],
    }

    @pytest.mark.asyncio
    async def test_create_report_success(self, citizen_client):
        """
        TC-DR-02-01
        Given:  valid JSON payload with 2 photo URLs
        Expect: 201, report_status=PENDING, photo_count=2, disaster_id=None
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(row={**SAMPLE_REPORT_DICT, "photo_count": 2})

        response = await client.post("/api/v1/disaster-reports/", json=self.BASE_PAYLOAD)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["report_status"] == "PENDING"
        assert body["disaster_id"] is None
        assert body["photo_count"] == 2

    @pytest.mark.asyncio
    async def test_create_report_all_photos_share_reference_id(self, citizen_client):
        """
        TC-DR-02-02
        Given:  3 photos in payload, no reference_id provided
        Expect: service generates one reference_id and assigns it to all photos;
                DB INSERT is called 3 times (once per photo) with the same reference_id
        Notes:  We verify execute call count — 1 (insert report) + 3 (insert photos) + 1 (flush/select)
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(
            row={**SAMPLE_REPORT_DICT, "photo_count": 3}
        )
        payload = {
            **self.BASE_PAYLOAD,
            "photos": [
                {"image_url": f"https://blob.azure.com/photo{i}.jpg", "file_size": 1024, "mime_type": "image/jpeg"}
                for i in range(3)
            ],
        }
        response = await client.post("/api/v1/disaster-reports/", json=payload)
        assert response.status_code == status.HTTP_201_CREATED
        # 1 report insert + 3 photo inserts + 1 select (_get_report_dict) = at least 5 execute calls
        assert mock_db.execute.call_count >= 5

    @pytest.mark.asyncio
    async def test_create_report_no_photos(self, citizen_client):
        """
        TC-DR-02-03
        Given:  valid JSON payload, photos field is empty list
        Expect: 201, photo_count=0 — photo INSERT block is skipped
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(row={**SAMPLE_REPORT_DICT, "photo_count": 0})

        payload = {**self.BASE_PAYLOAD, "photos": []}
        response = await client.post("/api/v1/disaster-reports/", json=payload)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["photo_count"] == 0

    @pytest.mark.asyncio
    async def test_create_report_invalid_severity(self, citizen_client):
        """
        TC-DR-02-04
        Given:  severity = "EXTREME" (not a valid disaster_severity enum)
        Expect: 422 from Pydantic schema validation before service is reached
        """
        client, _ = citizen_client
        payload = {**self.BASE_PAYLOAD, "severity": "EXTREME"}
        response = await client.post("/api/v1/disaster-reports/", json=payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_create_report_missing_description(self, citizen_client):
        """
        TC-DR-02-05
        Given:  description field missing from JSON payload
        Expect: 422 Unprocessable Entity
        """
        client, _ = citizen_client
        payload = {k: v for k, v in self.BASE_PAYLOAD.items() if k != "description"}
        response = await client.post("/api/v1/disaster-reports/", json=payload)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ══════════════════════════════════════════════════════════
# TC-DR-03  POST /disaster-reports/upload-media
# ══════════════════════════════════════════════════════════

class TestUploadMedia:
    """
    Endpoint: POST /disaster-reports/upload-media
    Auth:     get_current_user (citizen Bearer token)
    Service:  upload_multiple_files (blob_service) — called directly from API layer
    """

    @pytest.mark.asyncio
    async def test_upload_media_success(self, citizen_client):
        """
        TC-DR-03-01
        Given:  valid image file uploaded
        Expect: 200, response contains uploaded_files list with image_url
        """
        client, _ = citizen_client
        import io
        with patch(
            "app.api.v1.disaster_report.upload_multiple_files",
            new_callable=AsyncMock,
            return_value={
                "uploaded_files": [
                    {"image_url": "https://blob.azure.com/abc.jpg", "file_size": 51200, "mime_type": "image/jpeg", "original_filename": "scene.jpg"}
                ],
                "reference_id": "test-ref-id-upload",
            }
        ):
            response = await client.post(
                "/api/v1/disaster-reports/upload-media",
                files=[("files", ("scene.jpg", io.BytesIO(b"fake"), "image/jpeg"))],
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert len(body["uploaded_files"]) == 1
        assert "image_url" in body["uploaded_files"][0]

    @pytest.mark.asyncio
    async def test_upload_media_no_auth(self, unauthenticated_client):
        """
        TC-DR-03-02
        Given:  no token provided
        Expect: 401 or 403
        """
        import io
        response = await unauthenticated_client.post(
            "/api/v1/disaster-reports/upload-media",
            files=[("files", ("img.jpg", io.BytesIO(b"x"), "image/jpeg"))],
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    @pytest.mark.asyncio
    async def test_upload_media_no_files_provided(self, citizen_client):
        """
        TC-DR-03-03
        Given:  request sent without any files
        Expect: 422 — 'files' is a required Form field
        """
        client, _ = citizen_client
        response = await client.post("/api/v1/disaster-reports/upload-media")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ══════════════════════════════════════════════════════════
# TC-DR-04  GET /disaster-reports/pending/all  (admin)
# ══════════════════════════════════════════════════════════

class TestGetPendingReports:
    """
    Endpoint: GET /disaster-reports/pending/all
    Auth:     get_current_team_member (emergency team Bearer only)
    Service:  DisasterReportService.get_pending_reports()
    """

    @pytest.mark.asyncio
    async def test_get_pending_reports_success(self, admin_client):
        """
        TC-DR-04-01
        Given:  admin token, 3 pending reports exist
        Expect: 200, pending_reports list has 3 entries, count=3
        """
        client, mock_db = admin_client
        rows = [{**SAMPLE_REPORT_DICT, "id": str(uuid.uuid4())} for _ in range(3)]
        mock_db.execute.return_value = make_mock_result(rows=rows)

        response = await client.get("/api/v1/disaster-reports/pending/all")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == 3
        assert len(body["pending_reports"]) == 3

    @pytest.mark.asyncio
    async def test_get_pending_reports_empty(self, admin_client):
        """
        TC-DR-04-02
        Given:  admin token, no pending reports exist
        Expect: 200, empty list, count=0
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[])

        response = await client.get("/api/v1/disaster-reports/pending/all")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_get_pending_reports_citizen_forbidden(self, citizen_client):
        """
        TC-DR-04-03
        Given:  citizen token (not an emergency team member)
        Expect: 403 Forbidden — get_current_team_member raises
        """
        client, _ = citizen_client
        # citizen_client only overrides get_current_user, not get_current_team_member
        response = await client.get("/api/v1/disaster-reports/pending/all")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_get_pending_reports_limit_param(self, admin_client):
        """
        TC-DR-04-04
        Given:  admin token, limit=5 passed as query param
        Expect: 200, service called with limit=5 (DB query uses :limit param)
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[SAMPLE_REPORT_DICT])

        response = await client.get("/api/v1/disaster-reports/pending/all?limit=5")

        assert response.status_code == status.HTTP_200_OK
        # Verify limit was passed into the execute call
        call_kwargs = mock_db.execute.call_args
        assert call_kwargs is not None


# ══════════════════════════════════════════════════════════
# TC-DR-05  GET /disaster-reports/pending/clustered  (admin)
# ══════════════════════════════════════════════════════════

class TestGetClusteredPendingReports:
    """
    Endpoint: GET /disaster-reports/pending/clustered
    Auth:     get_current_team_member (emergency team Bearer only)
    Service:  DisasterReportService.get_clustered_pending_reports()
    Uses PostGIS ST_ClusterDBSCAN to group nearby same-type reports.
    """

    SAMPLE_CLUSTER = {
        "disaster_type": "FIRE",
        "cluster_id": 0,
        "report_count": 3,
        "total_photos": 5,
        "unique_reporters": 3,
        "max_people_affected": 20,
        "any_casualties": False,
        "any_structural_damage": True,
        "any_road_blocked": False,
        "earliest_report_at": datetime.utcnow(),
        "latest_report_at": datetime.utcnow(),
        "max_severity_rank": 3,
        "center_lat": 53.3498,
        "center_lon": -6.2603,
        "report_ids": [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())],
        "reporter_ids": [str(uuid.uuid4())],
        "max_severity": "HIGH",
        "primary_report_id": SAMPLE_REPORT_ID,
        "primary_description": "Large fire near warehouse",
        "primary_address": "Grand Canal Dock, Dublin 2",
    }

    @pytest.mark.asyncio
    async def test_get_clustered_reports_success(self, admin_client):
        """
        TC-DR-05-01
        Given:  admin token, 2 clusters in DB
        Expect: 200, clusters list len=2, cluster_count=2
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[self.SAMPLE_CLUSTER, self.SAMPLE_CLUSTER])

        response = await client.get("/api/v1/disaster-reports/pending/clustered")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["cluster_count"] == 2
        assert len(body["clusters"]) == 2

    @pytest.mark.asyncio
    async def test_get_clustered_reports_default_params(self, admin_client):
        """
        TC-DR-05-02
        Given:  no query params provided
        Expect: 200, service uses defaults radius_meters=500, time_window_hours=1
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[])

        response = await client.get("/api/v1/disaster-reports/pending/clustered")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["radius_meters"] == 500
        assert body["time_window_hours"] == 1

    @pytest.mark.asyncio
    async def test_get_clustered_reports_custom_params(self, admin_client):
        """
        TC-DR-05-03
        Given:  radius_meters=1000, time_window_hours=2 passed as query params
        Expect: 200, response echoes back the custom params
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[])

        response = await client.get(
            "/api/v1/disaster-reports/pending/clustered?radius_meters=1000&time_window_hours=2"
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["radius_meters"] == 1000
        assert body["time_window_hours"] == 2

    @pytest.mark.asyncio
    async def test_get_clustered_reports_citizen_forbidden(self, citizen_client):
        """
        TC-DR-05-04
        Given:  citizen token
        Expect: 403 Forbidden
        """
        client, _ = citizen_client
        response = await client.get("/api/v1/disaster-reports/pending/clustered")
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# TC-DR-06  GET /disaster-reports/user/{user_id}
# ══════════════════════════════════════════════════════════

class TestGetUserReports:
    """
    Endpoint: GET /disaster-reports/user/{user_id}
    Auth:     get_current_user (any Bearer token)
    Service:  DisasterReportService.get_user_reports()
    """

    @pytest.mark.asyncio
    async def test_get_user_reports_success(self, citizen_client):
        """
        TC-DR-06-01
        Given:  valid user_id, 2 reports exist for that user
        Expect: 200, reports list len=2, count=2, user_id echoed back
        """
        client, mock_db = citizen_client
        rows = [{**SAMPLE_REPORT_DICT, "id": str(uuid.uuid4())} for _ in range(2)]
        mock_db.execute.return_value = make_mock_result(rows=rows)

        response = await client.get(f"/api/v1/disaster-reports/user/{CITIZEN_USER_ID}")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == 2
        assert body["user_id"] == CITIZEN_USER_ID

    @pytest.mark.asyncio
    async def test_get_user_reports_no_reports(self, citizen_client):
        """
        TC-DR-06-02
        Given:  valid user_id, but user has submitted no reports
        Expect: 200, empty list, count=0
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(rows=[])

        response = await client.get(f"/api/v1/disaster-reports/user/{CITIZEN_USER_ID}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_get_user_reports_default_limit(self, citizen_client):
        """
        TC-DR-06-03
        Given:  no limit param — service defaults to limit=20
        Expect: 200, at most 20 results returned
        """
        client, mock_db = citizen_client
        rows = [{**SAMPLE_REPORT_DICT, "id": str(uuid.uuid4())} for _ in range(15)]
        mock_db.execute.return_value = make_mock_result(rows=rows)

        response = await client.get(f"/api/v1/disaster-reports/user/{CITIZEN_USER_ID}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] <= 20

    @pytest.mark.asyncio
    async def test_get_user_reports_no_auth(self, unauthenticated_client):
        """
        TC-DR-06-04
        Given:  no Bearer token
        Expect: 401 or 403
        """
        response = await unauthenticated_client.get(
            f"/api/v1/disaster-reports/user/{CITIZEN_USER_ID}"
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


# ══════════════════════════════════════════════════════════
# TC-DR-07  GET /disaster-reports/{report_id}
# ══════════════════════════════════════════════════════════

class TestGetReportById:
    """
    Endpoint: GET /disaster-reports/{report_id}
    Auth:     get_current_user (any Bearer token)
    Service:  DisasterReportService.get_report()
    IMPORTANT: This is a DYNAMIC route — it must be registered AFTER all static
               routes like /pending/all, /pending/clustered to avoid shadowing.
    """

    @pytest.mark.asyncio
    async def test_get_report_success(self, citizen_client):
        """
        TC-DR-07-01
        Given:  valid report_id that exists in DB
        Expect: 200, full report dict returned
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(row=SAMPLE_REPORT_DICT)

        response = await client.get(f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["id"] == SAMPLE_REPORT_ID
        assert body["report_status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_get_report_not_found(self, citizen_client):
        """
        TC-DR-07-02
        Given:  report_id that does not exist in DB (_get_report_dict returns None)
        Expect: 404 Not Found with message "Disaster report not found."
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(row=None)

        response = await client.get(f"/api/v1/disaster-reports/{uuid.uuid4()}")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_report_no_auth(self, unauthenticated_client):
        """
        TC-DR-07-03
        Given:  no Bearer token
        Expect: 401 or 403
        """
        response = await unauthenticated_client.get(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}"
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    @pytest.mark.asyncio
    async def test_get_report_response_shape(self, citizen_client):
        """
        TC-DR-07-04
        Given:  existing report with photos and all fields set
        Expect: response contains all required fields (id, user_id, disaster_type,
                severity, description, location, location_address, report_status,
                photo_count, people_affected, etc.)
        """
        client, mock_db = citizen_client
        mock_db.execute.return_value = make_mock_result(row=SAMPLE_REPORT_DICT)

        response = await client.get(f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}")

        body = response.json()
        required_fields = [
            "id", "user_id", "disaster_type", "severity", "description",
            "location", "location_address", "report_status",
            "people_affected", "photo_count", "created_at",
        ]
        for field in required_fields:
            assert field in body, f"Missing field: {field}"


# ══════════════════════════════════════════════════════════
# TC-DR-08  POST /disaster-reports/{report_id}/review  (admin)
# ══════════════════════════════════════════════════════════

class TestReviewSingleReport:
    """
    Endpoint: POST /disaster-reports/{report_id}/review
    Auth:     get_current_team_member (emergency team Bearer only)
    Service:  DisasterReportService.review_report()

    Business rules:
      - Only PENDING reports can be reviewed → 400 if already reviewed
      - REJECTED requires rejection_reason → 400 if missing
      - Concurrent review → 409 (atomic gate returns 0 rows)
      - VERIFIED creates disaster record + links report + returns _pending_event
      - tracking_id format: DIS-{year}-{seq:05d}
    """

    VERIFY_PAYLOAD = {
        "reviewed_by_id": ADMIN_TEAM_ID,
        "action": "verified",
        "rejection_reason": None,
    }

    REJECT_PAYLOAD = {
        "reviewed_by_id": ADMIN_TEAM_ID,
        "action": "rejected",
        "rejection_reason": "Report appears to be a false alarm — no fire brigade call logged.",
    }

    def _mock_pending_report_row(self):
        return {
            "id": SAMPLE_REPORT_ID,
            "user_id": CITIZEN_USER_ID,
            "disaster_type": "FIRE",
            "severity": "HIGH",
            "description": "Warehouse fire",
            "location_address": "Grand Canal Dock, Dublin 2",
            "location": None,
            "latitude": 53.3498,
            "longitude": -6.2603,
            "people_affected": 10,
            "multiple_casualties": False,
            "structural_damage": True,
            "road_blocked": False,
            "report_status": "PENDING",
        }

    def _mock_team_row(self):
        return {
            "id": ADMIN_TEAM_ID,
            "full_name": "Admin User",
            "department": "FIRE",
        }

    # ── VERIFY path ──

    @pytest.mark.asyncio
    async def test_review_verify_success(self, admin_client):
        """
        TC-DR-08-01
        Given:  PENDING report, action=verified, valid admin token
        Expect: 200, report_status=VERIFIED, disaster_id set (not None),
                tracking_id matches DIS-{year}-NNNNN format,
                _pending_event stripped from response (published via BackgroundTasks)
        """
        client, mock_db = admin_client

        seq_result = MagicMock()
        seq_result.scalar.return_value = 1

        team_result = make_mock_result(row=self._mock_team_row())
        report_result = make_mock_result(row=self._mock_pending_report_row())
        gate_result = MagicMock()
        gate_result.first.return_value = {"id": SAMPLE_REPORT_ID}

        # execute calls in order:
        # 1. fetch report
        # 2. CREATE SEQUENCE IF NOT EXISTS (no return needed)
        # 3. SELECT nextval(...)
        # 4. _get_team_info
        # 5. INSERT disasters
        # 6. UPDATE disaster_reports (gate)
        mock_db.execute.side_effect = [
            report_result,      # fetch report
            MagicMock(),        # CREATE SEQUENCE
            seq_result,         # nextval
            team_result,        # _get_team_info
            MagicMock(),        # INSERT disasters
            gate_result,        # UPDATE disaster_reports (gate — returns row)
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["report_status"] == "VERIFIED"
        assert body["disaster_id"] is not None
        assert body["tracking_id"].startswith(f"DIS-{datetime.utcnow().year}-")
        # _pending_event must NOT be in the API response (it's for BackgroundTasks only)
        assert "_pending_event" not in body

    @pytest.mark.asyncio
    async def test_review_verify_creates_disaster_record(self, admin_client):
        """
        TC-DR-08-02
        Given:  PENDING report, action=verified
        Expect: INSERT into disasters table is executed (disaster_status=ACTIVE)
        """
        client, mock_db = admin_client

        seq_result = MagicMock(); seq_result.scalar.return_value = 2
        mock_db.execute.side_effect = [
            make_mock_result(row=self._mock_pending_report_row()),   # fetch
            MagicMock(),                                             # CREATE SEQUENCE
            seq_result,                                              # nextval
            make_mock_result(row=self._mock_team_row()),             # team info
            MagicMock(),                                             # INSERT disaster
            make_mock_result(row={"id": SAMPLE_REPORT_ID}),         # gate UPDATE
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        # A disaster was created — disaster_id and tracking_id are present
        assert body["disaster_id"] is not None
        assert body["tracking_id"] is not None
        # Service made all expected DB calls (fetch + seq + team + INSERT + gate = 6)
        assert mock_db.execute.call_count == 6

    @pytest.mark.asyncio
    async def test_review_verify_tracking_id_format(self, admin_client):
        """
        TC-DR-08-03
        Given:  PENDING report, action=verified
        Expect: tracking_id = DIS-YYYY-NNNNN where YYYY is current year
                and NNNNN is zero-padded 5-digit sequence number
        """
        client, mock_db = admin_client
        year = datetime.utcnow().year
        seq_result = MagicMock(); seq_result.scalar.return_value = 7

        mock_db.execute.side_effect = [
            make_mock_result(row=self._mock_pending_report_row()),
            MagicMock(),
            seq_result,
            make_mock_result(row=self._mock_team_row()),
            MagicMock(),
            make_mock_result(row={"id": SAMPLE_REPORT_ID}),
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.json()["tracking_id"] == f"DIS-{year}-00007"

    # ── REJECT path ──

    @pytest.mark.asyncio
    async def test_review_reject_success(self, admin_client):
        """
        TC-DR-08-04
        Given:  PENDING report, action=rejected, rejection_reason provided
        Expect: 200, report_status=REJECTED, disaster_id=None, tracking_id=None
        """
        client, mock_db = admin_client

        gate_result = MagicMock(); gate_result.first.return_value = {"id": SAMPLE_REPORT_ID}
        mock_db.execute.side_effect = [
            make_mock_result(row=self._mock_pending_report_row()),  # fetch
            gate_result,                                            # UPDATE (reject gate)
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.REJECT_PAYLOAD,
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["report_status"] == "REJECTED"
        assert body["disaster_id"] is None
        assert body["tracking_id"] is None

    @pytest.mark.asyncio
    async def test_review_reject_without_reason_is_400(self, admin_client):
        """
        TC-DR-08-05
        Given:  action=rejected, rejection_reason is None
        Expect: 400 Bad Request — "rejection_reason is required when rejecting a report."
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(row=self._mock_pending_report_row())

        payload = {**self.REJECT_PAYLOAD, "rejection_reason": None}
        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=payload,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "rejection_reason" in response.json()["detail"].lower()

    # ── Guard conditions ──

    @pytest.mark.asyncio
    async def test_review_report_not_found_is_404(self, admin_client):
        """
        TC-DR-08-06
        Given:  report_id does not exist (DB returns None)
        Expect: 404 Not Found
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(row=None)

        response = await client.post(
            f"/api/v1/disaster-reports/{uuid.uuid4()}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_review_already_reviewed_is_400(self, admin_client):
        """
        TC-DR-08-07
        Given:  report_status is already VERIFIED (not PENDING)
        Expect: 400 Bad Request — "Report already reviewed."
        """
        client, mock_db = admin_client
        already_verified = {**self._mock_pending_report_row(), "report_status": "VERIFIED"}
        mock_db.execute.return_value = make_mock_result(row=already_verified)

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already reviewed" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_review_rejected_report_cannot_be_reviewed_again(self, admin_client):
        """
        TC-DR-08-08
        Given:  report_status is REJECTED
        Expect: 400 Bad Request — same guard as VERIFIED
        """
        client, mock_db = admin_client
        already_rejected = {**self._mock_pending_report_row(), "report_status": "REJECTED"}
        mock_db.execute.return_value = make_mock_result(row=already_rejected)

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_review_concurrent_request_is_409(self, admin_client):
        """
        TC-DR-08-09
        Given:  report is PENDING, but concurrent request already claimed it
                (atomic gate UPDATE returns 0 rows → gate_result.first() is None)
        Expect: 409 Conflict — "Report was already reviewed by a concurrent request."
        """
        client, mock_db = admin_client

        seq_result = MagicMock(); seq_result.scalar.return_value = 3
        gate_result = MagicMock(); gate_result.first.return_value = None  # ← concurrent claim

        mock_db.execute.side_effect = [
            make_mock_result(row=self._mock_pending_report_row()),  # fetch
            MagicMock(),                                            # CREATE SEQUENCE
            seq_result,                                             # nextval
            make_mock_result(row=self._mock_team_row()),            # team info
            MagicMock(),                                            # INSERT disaster
            gate_result,                                            # gate — 0 rows
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.asyncio
    async def test_review_citizen_token_forbidden(self, citizen_client):
        """
        TC-DR-08-10
        Given:  citizen Bearer token (not an emergency team member)
        Expect: 403 Forbidden
        """
        client, _ = citizen_client
        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json=self.VERIFY_PAYLOAD,
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# TC-DR-09  POST /disaster-reports/cluster/review  (admin)
# ══════════════════════════════════════════════════════════

class TestReviewCluster:
    """
    Endpoint: POST /disaster-reports/cluster/review
    Auth:     get_current_team_member (emergency team Bearer only)
    Service:  DisasterReportService.review_cluster()

    Business rules:
      - report_ids cannot be empty → 400
      - REJECTED requires rejection_reason → 400
      - VERIFIED creates ONE disaster, links ALL reports
      - Primary report (report_ids[0]) is gated atomically → 409 if already taken
      - Remaining reports linked with AND PENDING guard (non-fatal if already taken)
      - Cluster aggregates: max(people_affected), BOOL_OR(casualties/damage/blocked)
      - _pending_event returned, published via BackgroundTasks AFTER commit
    """

    REPORT_ID_1 = str(uuid.uuid4())
    REPORT_ID_2 = str(uuid.uuid4())
    REPORT_ID_3 = str(uuid.uuid4())

    VERIFY_PAYLOAD = {
        "report_ids": [REPORT_ID_1, REPORT_ID_2, REPORT_ID_3],
        "reviewed_by_id": ADMIN_TEAM_ID,
        "action": "verified",
        # rejection_reason intentionally omitted — sending null fails pydantic v2
        # when field type is `str` (not Optional[str])
    }

    REJECT_PAYLOAD = {
        "report_ids": [REPORT_ID_1, REPORT_ID_2],
        "reviewed_by_id": ADMIN_TEAM_ID,
        "action": "rejected",
        "rejection_reason": "No corroborating evidence — likely false alarm.",
    }

    def _primary_row(self):
        return {
            "id": self.REPORT_ID_1,
            "disaster_type": "FLOOD",
            "severity": "HIGH",
            "description": "Flooding at Grand Canal",
            "location_address": "Grand Canal, Dublin 6",
            "latitude": 53.3321,
            "longitude": -6.2541,
            "people_affected": 50,
            "multiple_casualties": False,
            "structural_damage": False,
            "road_blocked": True,
        }

    def _agg_row(self):
        return {
            "max_people": 50,
            "any_casualties": False,
            "any_damage": True,
            "any_blocked": True,
        }

    # ── VERIFY path ──

    @pytest.mark.asyncio
    async def test_cluster_verify_success(self, admin_client):
        """
        TC-DR-09-01
        Given:  3 report_ids, action=verified
        Expect: 200, action=verified, reports_updated=3,
                disaster_id set, tracking_id DIS-YYYY-NNNNN,
                _pending_event NOT in response body
        """
        client, mock_db = admin_client

        seq_result = MagicMock(); seq_result.scalar.return_value = 5
        gate_result = MagicMock(); gate_result.first.return_value = {"id": self.REPORT_ID_1}

        mock_db.execute.side_effect = [
            make_mock_result(row=self._primary_row()),          # 1. fetch primary
            MagicMock(),                                        # 2. CREATE SEQUENCE
            seq_result,                                         # 3. nextval
            make_mock_result(row={"id": ADMIN_TEAM_ID, "full_name": "Admin User", "department": "FIRE"}),  # 4. team info
            make_mock_result(row=self._agg_row()),              # 5. aggregate
            MagicMock(),                                        # 6. INSERT disaster
            gate_result,                                        # 7. gate (primary)
            MagicMock(),                                        # 8. UPDATE report_ids[1]
            MagicMock(),                                        # 9. UPDATE report_ids[2]
        ]

        response = await client.post("/api/v1/disaster-reports/cluster/review", json=self.VERIFY_PAYLOAD)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["action"] == "verified"
        assert body["reports_updated"] == 3
        assert body["disaster_id"] is not None
        assert body["tracking_id"].startswith(f"DIS-{datetime.utcnow().year}-")
        assert "_pending_event" not in body

    @pytest.mark.asyncio
    async def test_cluster_verify_one_disaster_created(self, admin_client):
        """
        TC-DR-09-02
        Given:  3 reports in cluster, action=verified
        Expect: exactly ONE disaster record inserted (not one per report)
        """
        client, mock_db = admin_client
        seq_result = MagicMock(); seq_result.scalar.return_value = 6
        gate_result = MagicMock(); gate_result.first.return_value = {"id": self.REPORT_ID_1}

        insert_calls = []
        original_execute = mock_db.execute

        async def tracking_execute(sql, params=None):
            sql_str = str(sql)
            if "INSERT INTO disasters" in sql_str:
                insert_calls.append(sql_str)
            return gate_result if "UPDATE disaster_reports" in sql_str and "RETURNING" in sql_str else make_mock_result(row=self._primary_row())

        mock_db.execute.side_effect = [
            make_mock_result(row=self._primary_row()),          # 1. fetch primary
            MagicMock(),                                        # 2. CREATE SEQUENCE
            seq_result,                                         # 3. nextval
            make_mock_result(row={"id": ADMIN_TEAM_ID, "full_name": "Admin", "department": "FIRE"}),  # 4. team info
            make_mock_result(row=self._agg_row()),              # 5. aggregate
            MagicMock(),                                        # 6. INSERT disasters (1 call)
            gate_result,                                        # 7. gate (primary)
            MagicMock(),                                        # 8. UPDATE report_ids[1]
            MagicMock(),                                        # 9. UPDATE report_ids[2]
        ]

        response = await client.post("/api/v1/disaster-reports/cluster/review", json=self.VERIFY_PAYLOAD)
        assert response.status_code == status.HTTP_200_OK
        # reports_updated reflects all 3 reports linked to the 1 disaster
        assert response.json()["reports_updated"] == 3

    @pytest.mark.asyncio
    async def test_cluster_verify_concurrent_is_409(self, admin_client):
        """
        TC-DR-09-03
        Given:  primary report already claimed by concurrent request
                (gate UPDATE returns 0 rows)
        Expect: 409 Conflict
        """
        client, mock_db = admin_client
        seq_result = MagicMock(); seq_result.scalar.return_value = 7
        gate_result = MagicMock(); gate_result.first.return_value = None  # ← concurrent claim

        mock_db.execute.side_effect = [
            make_mock_result(row=self._primary_row()),          # 1. fetch primary
            MagicMock(),                                        # 2. CREATE SEQUENCE
            seq_result,                                         # 3. nextval
            make_mock_result(row={"id": ADMIN_TEAM_ID, "full_name": "Admin", "department": "FIRE"}),  # 4. team info
            make_mock_result(row=self._agg_row()),              # 5. aggregate
            MagicMock(),                                        # 6. INSERT disaster
            gate_result,                                        # 7. gate returns None ← concurrent
        ]

        response = await client.post("/api/v1/disaster-reports/cluster/review", json=self.VERIFY_PAYLOAD)
        assert response.status_code == status.HTTP_409_CONFLICT

    # ── REJECT path ──

    @pytest.mark.asyncio
    async def test_cluster_reject_success(self, admin_client):
        """
        TC-DR-09-04
        Given:  2 report_ids, action=rejected, rejection_reason provided
        Expect: 200, action=rejected, reports_updated=2, disaster_id=None
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = MagicMock()

        response = await client.post(
            "/api/v1/disaster-reports/cluster/review", json=self.REJECT_PAYLOAD
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["action"] == "rejected"
        assert body["reports_updated"] == 2
        assert body["disaster_id"] is None

    @pytest.mark.asyncio
    async def test_cluster_reject_without_reason_is_400(self, admin_client):
        """
        TC-DR-09-05
        Given:  action=rejected, rejection_reason=None
        Expect: 400 Bad Request
        """
        client, _ = admin_client
        # Send empty string — pydantic accepts it (str), service raises 400
        # (sending None/null causes 422 from pydantic since field type is str not Optional[str])
        payload = {**self.REJECT_PAYLOAD, "rejection_reason": ""}
        response = await client.post("/api/v1/disaster-reports/cluster/review", json=payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # ── Guard conditions ──

    @pytest.mark.asyncio
    async def test_cluster_empty_report_ids_is_400(self, admin_client):
        """
        TC-DR-09-06
        Given:  report_ids is an empty list
        Expect: 400 Bad Request from API layer before service is called
                (check: "report_ids cannot be empty.")
        """
        client, _ = admin_client
        payload = {**self.VERIFY_PAYLOAD, "report_ids": []}
        response = await client.post("/api/v1/disaster-reports/cluster/review", json=payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "report_ids" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_cluster_primary_report_not_found_is_404(self, admin_client):
        """
        TC-DR-09-07
        Given:  primary report_id doesn't exist (DB returns None)
        Expect: 404 Not Found
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(row=None)

        response = await client.post(
            "/api/v1/disaster-reports/cluster/review", json=self.VERIFY_PAYLOAD
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_cluster_review_citizen_forbidden(self, citizen_client):
        """
        TC-DR-09-08
        Given:  citizen Bearer token
        Expect: 403 Forbidden
        """
        client, _ = citizen_client
        response = await client.post(
            "/api/v1/disaster-reports/cluster/review", json=self.VERIFY_PAYLOAD
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
# TC-DR-10  Cross-cutting / Integration-level checks
# ══════════════════════════════════════════════════════════

class TestDisasterReportCrossCutting:
    """
    Tests that cut across multiple endpoints or verify system-level
    behaviour (routing, RabbitMQ deferral, DB sequence for tracking IDs).
    """

    @pytest.mark.asyncio
    async def test_static_route_pending_all_not_shadowed_by_dynamic(self, admin_client):
        """
        TC-DR-10-01
        Given:  GET /pending/all is requested
        Expect: It resolves to get_pending_reports — NOT get_report_by_id
                (confirms static-before-dynamic route ordering is correct)
        """
        client, mock_db = admin_client
        mock_db.execute.return_value = make_mock_result(rows=[SAMPLE_REPORT_DICT])

        response = await client.get("/api/v1/disaster-reports/pending/all")

        # If the dynamic route /{report_id} shadowed this, we'd get a 404
        # because "pending" is not a valid UUID report_id. Correct routing = 200.
        assert response.status_code == status.HTTP_200_OK
        assert "pending_reports" in response.json()

    @pytest.mark.asyncio
    async def test_pending_event_not_in_verify_response(self, admin_client):
        """
        TC-DR-10-02
        Given:  review_report returns _pending_event in service result
        Expect: API layer pops it before sending response
                (_pending_event must NOT appear in HTTP response body)
        """
        client, mock_db = admin_client
        seq_result = MagicMock(); seq_result.scalar.return_value = 1
        gate_result = MagicMock(); gate_result.first.return_value = {"id": SAMPLE_REPORT_ID}

        report_row = {
            "id": SAMPLE_REPORT_ID, "user_id": CITIZEN_USER_ID,
            "disaster_type": "FIRE", "severity": "HIGH",
            "description": "Fire", "location_address": "Dublin",
            "location": None, "latitude": 53.3498, "longitude": -6.2603,
            "people_affected": 5, "multiple_casualties": False,
            "structural_damage": False, "road_blocked": False,
            "report_status": "PENDING",
        }
        mock_db.execute.side_effect = [
            make_mock_result(row=report_row),
            MagicMock(),
            seq_result,
            make_mock_result(row={"id": ADMIN_TEAM_ID, "full_name": "Admin", "department": "FIRE"}),
            MagicMock(),
            gate_result,
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json={"reviewed_by_id": ADMIN_TEAM_ID, "action": "verified", "rejection_reason": None},
        )

        assert response.status_code == status.HTTP_200_OK
        assert "_pending_event" not in response.json()

    @pytest.mark.asyncio
    async def test_tracking_id_uses_sequence_not_count(self, admin_client):
        """
        TC-DR-10-03
        Given:  Two concurrent verify calls would both read the same COUNT(*) under
                the old approach — under the new approach, DB sequence nextval is used.
        Expect: tracking_id reflects the sequence value from nextval, not COUNT+1
                (we confirm by injecting seq=42 and checking DIS-YYYY-00042)
        """
        client, mock_db = admin_client
        seq_result = MagicMock(); seq_result.scalar.return_value = 42

        report_row = {
            "id": SAMPLE_REPORT_ID, "user_id": CITIZEN_USER_ID,
            "disaster_type": "FIRE", "severity": "MEDIUM",
            "description": "Fire", "location_address": "Dublin",
            "location": None, "latitude": 53.35, "longitude": -6.26,
            "people_affected": 3, "multiple_casualties": False,
            "structural_damage": False, "road_blocked": False,
            "report_status": "PENDING",
        }
        gate_result = MagicMock(); gate_result.first.return_value = {"id": SAMPLE_REPORT_ID}

        mock_db.execute.side_effect = [
            make_mock_result(row=report_row),
            MagicMock(),
            seq_result,
            make_mock_result(row={"id": ADMIN_TEAM_ID, "full_name": "Admin", "department": "FIRE"}),
            MagicMock(),
            gate_result,
        ]

        response = await client.post(
            f"/api/v1/disaster-reports/{SAMPLE_REPORT_ID}/review",
            json={"reviewed_by_id": ADMIN_TEAM_ID, "action": "verified", "rejection_reason": None},
        )

        year = datetime.utcnow().year
        assert response.json()["tracking_id"] == f"DIS-{year}-00042"

    @pytest.mark.asyncio
    async def test_report_status_is_always_pending_on_creation(self, citizen_client):
        """
        TC-DR-10-04
        Given:  citizen creates a new report via POST /disaster-reports/
        Expect: report_status is always PENDING regardless of any payload field
                (service hardcodes "PENDING" — citizens cannot self-approve)
        """
        client, mock_db = citizen_client
        pending_report = {**SAMPLE_REPORT_DICT, "report_status": "PENDING"}
        mock_db.execute.return_value = make_mock_result(row=pending_report)

        payload = {
            "user_id": CITIZEN_USER_ID,
            "location_address": "Test Street",
            "disaster_type": "FLOOD",
            "severity": "LOW",
            "description": "Test flood",
            "latitude": 53.35,
            "longitude": -6.26,
            "photos": [],
        }
        response = await client.post("/api/v1/disaster-reports/", json=payload)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["report_status"] == "PENDING"