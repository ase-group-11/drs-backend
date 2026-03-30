"""
tests/unit/test_deploy_service.py

Unit tests for DeployService (UC6 new logic).

All DB interactions are mocked with AsyncMock — no real database needed.
Tests cover: suggested units, GPS update, unit positions, recall.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.services.deploy_service import DeployService, _haversine_km


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_execute(rows_by_call: dict):
    """
    Build a mock db.execute that returns different results per call number.

    rows_by_call: {1: <single-row dict or None>, 2: <list of row dicts>, ...}

    A value of None means .first() returns None (record not found).
    A list means .all() returns that list.
    A dict means .first() returns that dict.
    """
    call_count = [0]

    async def _execute(stmt, params=None):
        call_count[0] += 1
        n = call_count[0]
        data = rows_by_call.get(n, None)

        result = MagicMock()
        if isinstance(data, list):
            result.mappings.return_value.all.return_value   = data
            result.mappings.return_value.first.return_value = data[0] if data else None
        else:
            result.mappings.return_value.first.return_value = data
            result.mappings.return_value.all.return_value   = [data] if data else []
        return result

    return _execute


def _make_db(rows_by_call: dict):
    db = AsyncMock()
    db.execute.side_effect = _make_execute(rows_by_call)
    db.flush = AsyncMock()
    return db


# ─────────────────────────────────────────────────────────────────────────────
# Tests: get_suggested_units
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSuggestedUnits:

    @pytest.mark.asyncio
    async def test_returns_correct_suggestions_for_fire_high(self):
        """FIRE + HIGH should include fire engines, ambulance, patrol car."""
        db = _make_db({
            1: {   # disaster query
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "FIRE", "severity": "HIGH",
                "disaster_status": "ACTIVE",
                "multiple_casualties": False,
                "road_blocked": False,
                "location_address": "O'Connell Street, Dublin 1",
                "lat": 53.34, "lon": -6.26,
            },
            2: [   # available unit counts
                {"unit_type": "FIRE_ENGINE", "available_count": 3},
                {"unit_type": "AMBULANCE",   "available_count": 2},
                {"unit_type": "PATROL_CAR",  "available_count": 1},
            ],
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        assert result["disaster_type"] == "FIRE"
        assert result["severity"]      == "HIGH"
        types = [s["unit_type"] for s in result["suggestions"]]
        assert "FIRE_ENGINE" in types
        assert "AMBULANCE"   in types
        assert "PATROL_CAR"  in types

    @pytest.mark.asyncio
    async def test_shortage_flagged_when_not_enough_units(self):
        """If available < required, has_shortage=True and shortage count is correct."""
        db = _make_db({
            1: {
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "FIRE", "severity": "CRITICAL",   # needs 3 fire engines
                "disaster_status": "ACTIVE",
                "multiple_casualties": False, "road_blocked": False,
                "location_address": "Dublin", "lat": 53.34, "lon": -6.26,
            },
            2: [
                {"unit_type": "FIRE_ENGINE", "available_count": 1},  # only 1, need 3
            ],
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        assert result["has_shortage"] is True
        fire = next(s for s in result["suggestions"] if s["unit_type"] == "FIRE_ENGINE")
        assert fire["shortage"]   == 2
        assert fire["has_shortage"] is True

    @pytest.mark.asyncio
    async def test_no_shortage_when_sufficient_units(self):
        db = _make_db({
            1: {
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "FIRE", "severity": "LOW",   # needs only 1 fire engine
                "disaster_status": "ACTIVE",
                "multiple_casualties": False, "road_blocked": False,
                "location_address": "Dublin", "lat": 53.34, "lon": -6.26,
            },
            2: [
                {"unit_type": "FIRE_ENGINE", "available_count": 5},
            ],
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        assert result["has_shortage"] is False
        for s in result["suggestions"]:
            assert s["shortage"] == 0

    @pytest.mark.asyncio
    async def test_extra_ambulance_added_for_multiple_casualties(self):
        """FLOOD + LOW only suggests RESCUE — but multiple_casualties adds AMBULANCE."""
        db = _make_db({
            1: {
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "FLOOD", "severity": "LOW",
                "disaster_status": "ACTIVE",
                "multiple_casualties": True,   # ← triggers extra AMBULANCE
                "road_blocked": False,
                "location_address": "Dublin", "lat": 53.34, "lon": -6.26,
            },
            2: [],   # no units available
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        types = [s["unit_type"] for s in result["suggestions"]]
        assert "AMBULANCE" in types, "Expected AMBULANCE to be added for multiple_casualties"

    @pytest.mark.asyncio
    async def test_extra_patrol_car_added_for_road_blocked(self):
        """road_blocked adds PATROL_CAR if not already in suggestions."""
        db = _make_db({
            1: {
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "MEDICAL_EMERGENCY", "severity": "LOW",  # only AMBULANCE by default
                "disaster_status": "ACTIVE",
                "multiple_casualties": False,
                "road_blocked": True,   # ← triggers extra PATROL_CAR
                "location_address": "Dublin", "lat": 53.34, "lon": -6.26,
            },
            2: [],
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        types = [s["unit_type"] for s in result["suggestions"]]
        assert "PATROL_CAR" in types, "Expected PATROL_CAR added for road_blocked"

    @pytest.mark.asyncio
    async def test_no_duplicate_types_from_flags(self):
        """Flags should not add a type that's already in base suggestions."""
        db = _make_db({
            1: {
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "ACCIDENT", "severity": "CRITICAL",  # already has AMBULANCE + PATROL_CAR
                "disaster_status": "ACTIVE",
                "multiple_casualties": True,
                "road_blocked": True,
                "location_address": "Dublin", "lat": 53.34, "lon": -6.26,
            },
            2: [],
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        types = [s["unit_type"] for s in result["suggestions"]]
        # No duplicates
        assert len(types) == len(set(types))

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_disaster(self):
        """Non-existent disaster_id raises HTTPException 404."""
        db = AsyncMock()
        not_found = MagicMock()
        not_found.mappings.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=not_found)

        from fastapi import HTTPException
        service = DeployService(db)
        with pytest.raises(HTTPException) as exc_info:
            await service.get_suggested_units("does-not-exist")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_uses_fallback_for_unknown_disaster_type(self):
        """Unrecognised disaster type uses default suggestions without crashing."""
        db = _make_db({
            1: {
                "id": "dis-1", "tracking_id": "DRS-001",
                "type": "ALIEN_INVASION", "severity": "HIGH",
                "disaster_status": "ACTIVE",
                "multiple_casualties": False, "road_blocked": False,
                "location_address": "Dublin", "lat": 53.34, "lon": -6.26,
            },
            2: [],
        })
        service = DeployService(db)
        result  = await service.get_suggested_units("dis-1")

        assert len(result["suggestions"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Tests: update_gps_location
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateGpsLocation:

    @pytest.mark.asyncio
    async def test_updates_gps_for_en_route_deployment(self):
        db = AsyncMock()
        dep_mock = MagicMock()
        dep_mock.mappings.return_value.first.return_value = {
            "id": "dep-1", "deployment_status": "EN_ROUTE", "disaster_id": "dis-1"
        }
        db.execute = AsyncMock(return_value=dep_mock)
        db.flush   = AsyncMock()

        service = DeployService(db)
        result  = await service.update_gps_location("dep-1", 53.34, -6.26, heading=90.0, speed_kmh=45.0)

        assert result["latitude"]   == 53.34
        assert result["longitude"]  == -6.26
        assert result["heading"]    == 90.0
        assert result["speed_kmh"]  == 45.0
        assert "updated_at" in result
        db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_gps_without_optional_heading_speed(self):
        db = AsyncMock()
        dep_mock = MagicMock()
        dep_mock.mappings.return_value.first.return_value = {
            "id": "dep-1", "deployment_status": "DISPATCHED", "disaster_id": "dis-1"
        }
        db.execute = AsyncMock(return_value=dep_mock)
        db.flush   = AsyncMock()

        service = DeployService(db)
        result  = await service.update_gps_location("dep-1", 53.34, -6.26)

        assert result["heading"]   is None
        assert result["speed_kmh"] is None

    @pytest.mark.asyncio
    async def test_rejects_completed_deployment(self):
        db = AsyncMock()
        dep_mock = MagicMock()
        dep_mock.mappings.return_value.first.return_value = {
            "id": "dep-1", "deployment_status": "COMPLETED", "disaster_id": "dis-1"
        }
        db.execute = AsyncMock(return_value=dep_mock)

        from fastapi import HTTPException
        service = DeployService(db)
        with pytest.raises(HTTPException) as exc_info:
            await service.update_gps_location("dep-1", 53.34, -6.26)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_cancelled_deployment(self):
        db = AsyncMock()
        dep_mock = MagicMock()
        dep_mock.mappings.return_value.first.return_value = {
            "id": "dep-1", "deployment_status": "CANCELLED", "disaster_id": "dis-1"
        }
        db.execute = AsyncMock(return_value=dep_mock)

        from fastapi import HTTPException
        service = DeployService(db)
        with pytest.raises(HTTPException) as exc_info:
            await service.update_gps_location("dep-1", 53.34, -6.26)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_404_for_missing_deployment(self):
        db = AsyncMock()
        dep_mock = MagicMock()
        dep_mock.mappings.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=dep_mock)

        from fastapi import HTTPException
        service = DeployService(db)
        with pytest.raises(HTTPException) as exc_info:
            await service.update_gps_location("bad-id", 53.34, -6.26)
        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Tests: get_unit_positions
# ─────────────────────────────────────────────────────────────────────────────

class TestGetUnitPositions:

    def _base_unit_row(self, **overrides):
        row = {
            "deployment_id":     "dep-1",
            "deployment_status": "EN_ROUTE",
            "current_latitude":  None,
            "current_longitude": None,
            "heading":           None,
            "speed_kmh":         None,
            "location_updated_at": None,
            "unit_id":   "unit-1",
            "unit_code": "F-01",
            "unit_name": "Fire Engine 1",
            "unit_type": "FIRE_ENGINE",
            "department": "FIRE",
            "station_lat": 53.3474,
            "station_lon": -6.2530,
        }
        row.update(overrides)
        return row

    def _make_pos_db(self, units_data):
        call_count = [0]
        async def _execute(stmt, params=None):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:   # disaster query
                result.mappings.return_value.first.return_value = {
                    "id": "dis-1", "lat": 53.3498, "lon": -6.2603
                }
            else:                    # units query
                result.mappings.return_value.all.return_value = units_data
            return result
        db = AsyncMock()
        db.execute.side_effect = _execute
        return db

    @pytest.mark.asyncio
    async def test_uses_gps_position_when_available(self):
        row = self._base_unit_row(
            current_latitude=53.35, current_longitude=-6.25,
            heading=45.0, speed_kmh=40.0,
            location_updated_at=datetime.now(tz=timezone.utc),
        )
        db = self._make_pos_db([row])
        service = DeployService(db)
        result  = await service.get_unit_positions("dis-1")

        assert result["unit_count"] == 1
        unit = result["units"][0]
        assert unit["position"]["is_gps"]    is True
        assert unit["position"]["latitude"]  == 53.35
        assert unit["position"]["longitude"] == -6.25

    @pytest.mark.asyncio
    async def test_falls_back_to_station_when_no_gps(self):
        row = self._base_unit_row()   # no current_latitude
        db  = self._make_pos_db([row])
        service = DeployService(db)
        result  = await service.get_unit_positions("dis-1")

        unit = result["units"][0]
        assert unit["position"]["is_gps"]   is False
        assert unit["position"]["latitude"]  == pytest.approx(53.3474, rel=1e-4)
        assert unit["position"]["longitude"] == pytest.approx(-6.2530, rel=1e-4)

    @pytest.mark.asyncio
    async def test_eta_calculated_for_dispatched_unit(self):
        row = self._base_unit_row(
            deployment_status="DISPATCHED",
            station_lat=53.30, station_lon=-6.30,
        )
        db  = self._make_pos_db([row])
        service = DeployService(db)
        result  = await service.get_unit_positions("dis-1")

        unit = result["units"][0]
        assert unit["eta_minutes"] is not None
        assert unit["eta_minutes"] > 0

    @pytest.mark.asyncio
    async def test_no_eta_for_on_scene_unit(self):
        row = self._base_unit_row(deployment_status="ON_SCENE")
        db  = self._make_pos_db([row])
        service = DeployService(db)
        result  = await service.get_unit_positions("dis-1")

        assert result["units"][0]["eta_minutes"] is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_active_deployments(self):
        db = self._make_pos_db([])
        service = DeployService(db)
        result  = await service.get_unit_positions("dis-1")

        assert result["unit_count"] == 0
        assert result["units"]      == []

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_disaster(self):
        db = AsyncMock()
        dis_mock = MagicMock()
        dis_mock.mappings.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=dis_mock)

        from fastapi import HTTPException
        service = DeployService(db)
        with pytest.raises(HTTPException) as exc_info:
            await service.get_unit_positions("bad-id")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_multiple_units_returned(self):
        rows = [
            self._base_unit_row(
                deployment_id="dep-1", unit_id="u1", unit_code="F-01",
                current_latitude=53.35, current_longitude=-6.25,
                deployment_status="EN_ROUTE",
            ),
            self._base_unit_row(
                deployment_id="dep-2", unit_id="u2", unit_code="A-01",
                unit_type="AMBULANCE", department="MEDICAL",
                deployment_status="DISPATCHED",
            ),
        ]
        db  = self._make_pos_db(rows)
        service = DeployService(db)
        result  = await service.get_unit_positions("dis-1")

        assert result["unit_count"] == 2
        codes = [u["unit_code"] for u in result["units"]]
        assert "F-01" in codes
        assert "A-01" in codes


# ─────────────────────────────────────────────────────────────────────────────
# Tests: recall_unit
# ─────────────────────────────────────────────────────────────────────────────

class TestRecallUnit:

    def _make_recall_db(self, dep_row):
        call_count = [0]
        async def _execute(stmt, params=None):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.mappings.return_value.first.return_value = dep_row
            else:
                result.mappings.return_value.first.return_value = {}
            return result
        db = AsyncMock()
        db.execute.side_effect = _execute
        db.flush = AsyncMock()
        return db

    def _sample_dep_row(self, status="ON_SCENE"):
        return {
            "id":                "dep-1",
            "disaster_id":       "dis-1",
            "unit_id":           "unit-1",
            "deployment_status": status,
            "tracking_id":       "DRS-001",
            "unit_code":         "F-01",
            "unit_name":         "Fire Engine 1",
        }

    @pytest.mark.asyncio
    async def test_recalls_on_scene_deployment(self):
        db      = self._make_recall_db(self._sample_dep_row("ON_SCENE"))
        service = DeployService(db)
        result  = await service.recall_unit("dep-1", "Situation resolved")

        assert result["new_status"]      == "CANCELLED"
        assert result["previous_status"] == "ON_SCENE"
        assert result["unit_code"]       == "F-01"
        assert "_pending_event" in result

    @pytest.mark.asyncio
    async def test_recalls_dispatched_deployment(self):
        db      = self._make_recall_db(self._sample_dep_row("DISPATCHED"))
        service = DeployService(db)
        result  = await service.recall_unit("dep-1", "Mission cancelled")

        assert result["new_status"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_pending_event_has_correct_topic_and_payload(self):
        db      = self._make_recall_db(self._sample_dep_row())
        service = DeployService(db)
        result  = await service.recall_unit("dep-1", "Downgrade")

        event = result["_pending_event"]
        assert event["topic"] == "disaster.unit_recalled"
        assert event["payload"]["disaster_id"]   == "dis-1"
        assert event["payload"]["unit_code"]     == "F-01"
        assert event["payload"]["deployment_id"] == "dep-1"
        assert event["payload"]["reason"]        == "Downgrade"

    @pytest.mark.asyncio
    async def test_raises_400_for_already_completed(self):
        db      = self._make_recall_db(self._sample_dep_row("COMPLETED"))
        service = DeployService(db)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await service.recall_unit("dep-1", "Test")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_400_for_already_cancelled(self):
        db      = self._make_recall_db(self._sample_dep_row("CANCELLED"))
        service = DeployService(db)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await service.recall_unit("dep-1", "Test")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_deployment(self):
        db = AsyncMock()
        not_found = MagicMock()
        not_found.mappings.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=not_found)

        from fastapi import HTTPException
        service = DeployService(db)
        with pytest.raises(HTTPException) as exc_info:
            await service.recall_unit("bad-id", "Test")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_flush_called_after_updates(self):
        db      = self._make_recall_db(self._sample_dep_row())
        service = DeployService(db)
        await service.recall_unit("dep-1", "Test")
        db.flush.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _haversine_km helper
# ─────────────────────────────────────────────────────────────────────────────

class TestHaversine:

    def test_same_point_is_zero(self):
        assert _haversine_km(53.34, -6.26, 53.34, -6.26) == pytest.approx(0.0, abs=0.001)

    def test_known_distance_dublin_cork(self):
        # Dublin → Cork is ~220 km straight line
        d = _haversine_km(53.3498, -6.2603, 51.8985, -8.4756)
        assert 210 < d < 235

    def test_symmetry(self):
        d1 = _haversine_km(53.34, -6.26, 53.40, -6.20)
        d2 = _haversine_km(53.40, -6.20, 53.34, -6.26)
        assert d1 == pytest.approx(d2, rel=1e-6)

    def test_short_distance_within_dublin(self):
        # Two points in Dublin ~1 km apart
        d = _haversine_km(53.3333, -6.2489, 53.3404, -6.2555)
        assert 0.5 < d < 2.0