# File: app/tests/unit/test_disaster_report_repository.py
"""
Unit tests for DisasterReportRepository.

Tests critical functionality:
1. create() saves disaster report correctly
2. get_by_id() retrieves specific report
3. get_user_reports() returns user's reports with pagination
4. count_user_reports() returns accurate count
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
import uuid

from app.repositories.disaster_report_repository import DisasterReportRepository
from app.db.models.disaster_report import DisasterReport
from app.db.models.enums import DisasterType, Severity, ReportStatus


@pytest.mark.asyncio
async def test_create_disaster_report(mock_db_session):
    """Test creating a disaster report."""
    # Arrange
    repo = DisasterReportRepository(mock_db_session)

    report_data = {
        "user_id": str(uuid.uuid4()),
        "location_address": "123 O'Connell Street, Dublin 1",
        "location_latitude": 53.3498,
        "location_longitude": -6.2603,
        "disaster_type": DisasterType.FIRE,
        "severity": Severity.HIGH,
        "description": "Large fire at commercial building",
        "media_urls": ["https://example.com/photo1.jpg"],
        "people_affected": 25,
        "multiple_casualties": True,
        "structural_damage": True,
        "hazmat_involved": False,
        "road_blocked": True,
        "status": ReportStatus.SUBMITTED
    }

    # Act
    result = await repo.create(report_data)

    # Assert
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_awaited_once()
    mock_db_session.refresh.assert_awaited_once()

    # Verify a DisasterReport was added
    added_report = mock_db_session.add.call_args[0][0]
    assert isinstance(added_report, DisasterReport)
    assert added_report.user_id == report_data["user_id"]
    assert added_report.disaster_type == DisasterType.FIRE
    assert added_report.severity == Severity.HIGH


@pytest.mark.asyncio
async def test_get_disaster_report_by_id(mock_db_session):
    """Test retrieving a disaster report by ID."""
    # Arrange
    repo = DisasterReportRepository(mock_db_session)
    report_id = str(uuid.uuid4())

    # Mock database response
    mock_report = DisasterReport()
    mock_report.id = report_id
    mock_report.disaster_type = DisasterType.FIRE
    mock_report.severity = Severity.HIGH

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_report
    mock_db_session.execute.return_value = mock_result

    # Act
    result = await repo.get_by_id(report_id)

    # Assert
    assert result is not None
    assert result.id == report_id
    assert result.disaster_type == DisasterType.FIRE
    mock_db_session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_user_reports(mock_db_session):
    """Test retrieving user's disaster reports with pagination."""
    # Arrange
    repo = DisasterReportRepository(mock_db_session)
    user_id = str(uuid.uuid4())

    # Mock reports
    mock_report1 = DisasterReport()
    mock_report1.id = str(uuid.uuid4())
    mock_report1.user_id = user_id
    mock_report1.disaster_type = DisasterType.FIRE

    mock_report2 = DisasterReport()
    mock_report2.id = str(uuid.uuid4())
    mock_report2.user_id = user_id
    mock_report2.disaster_type = DisasterType.FLOOD

    # Mock database response
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_report1, mock_report2]
    mock_result.scalars.return_value = mock_scalars
    mock_db_session.execute.return_value = mock_result

    # Act
    result = await repo.get_user_reports(
        user_id=user_id,
        skip=0,
        limit=20
    )

    # Assert
    assert len(result) == 2
    assert result[0].id == mock_report1.id
    assert result[1].id == mock_report2.id
    mock_db_session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_count_user_reports(mock_db_session):
    """Test counting user's disaster reports."""
    # Arrange
    repo = DisasterReportRepository(mock_db_session)
    user_id = str(uuid.uuid4())

    # Mock database response
    mock_result = MagicMock()
    mock_result.scalar.return_value = 5
    mock_db_session.execute.return_value = mock_result

    # Act
    result = await repo.count_user_reports(user_id)

    # Assert
    assert result == 5
    mock_db_session.execute.assert_awaited_once()
