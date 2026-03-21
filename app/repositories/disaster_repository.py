# File: app/repositories/disaster_repository.py
"""
Disaster Repository - Database access layer for VERIFIED disaster data.

IMPORTANT: This repository queries the 'disasters' table which contains
ONLY verified disasters (created by emergency team). 

For unverified user reports, use DisasterReportRepository instead.
"""

import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_, func, text, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from geoalchemy2.functions import (
    ST_MakeEnvelope, ST_Within, ST_Distance,
    ST_AsGeoJSON, ST_Point, ST_DWithin, ST_X, ST_Y
)
from geoalchemy2 import Geography

from app.db.models.disaster import Disaster
from app.db.models.enums import DisasterType, DisasterSeverity, DisasterStatus

logger = logging.getLogger(__name__)


class DisasterRepository:
    """
    Repository for accessing VERIFIED disaster data.
    
    All disasters in this table have been verified by emergency teams.
    For unverified user reports, use DisasterReportRepository.
    
    Provides methods to query disasters within bounding boxes, near points,
    and with various filters (type, severity, status, time range).
    """
    
    def __init__(self, db_session: AsyncSession):
        """
        Initialize the disaster repository.
        
        Args:
            db_session: Async SQLAlchemy database session
        """
        self.db = db_session
    
    async def list_active_disasters(
        self,
        bounds: str,
        disaster_types: Optional[List[str]] = None,
        min_severity: Optional[str] = None,
        max_age_hours: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query active VERIFIED disasters within a bounding box.
        
        **NOTE:** All disasters in the 'disasters' table are already verified.
        This table only contains disasters created/verified by emergency teams.
        
        Uses PostGIS spatial queries for efficient geospatial filtering.
        Returns disasters as dictionaries for easy JSON serialization.
        
        Args:
            bounds: Bounding box string "south,west,north,east"
            disaster_types: Optional filter by disaster types
            min_severity: Optional minimum severity
            max_age_hours: Optional filter disasters newer than N hours
            
        Returns:
            List of verified disaster dicts with all fields including geometry
            
        Example:
            disasters = await repo.list_active_disasters(
                bounds="53.30,-6.35,53.40,-6.20",
                disaster_types=["flood", "fire"],
                min_severity="medium"
            )
        """
        logger.info(f"Querying active disasters for bounds: {bounds}")

        try:
            south, west, north, east = map(float, bounds.split(','))

            conditions = [
                "d.disaster_status = CAST('ACTIVE' AS disaster_status)",
                "d.deleted_at IS NULL",
                "ST_Within(d.location::geometry, ST_MakeEnvelope(:west, :south, :east, :north, 4326))",
            ]
            params: dict = {
                "south": south, "west": west, "north": north, "east": east,
            }

            if disaster_types:
                valid = [t.upper() for t in disaster_types]
                conditions.append("d.type::text = ANY(:disaster_types)")
                params["disaster_types"] = valid

            severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
            if min_severity and min_severity.upper() in severity_rank:
                min_rank = severity_rank[min_severity.upper()]
                allowed = [s for s, r in severity_rank.items() if r >= min_rank]
                conditions.append("d.severity::text = ANY(:severities)")
                params["severities"] = allowed

            if max_age_hours:
                conditions.append("d.created_at >= NOW() - INTERVAL ':hours hours'")
                params["hours"] = max_age_hours

            where_clause = " AND ".join(conditions)

            sql = text(f"""
                SELECT
                    d.id, d.tracking_id, d.type, d.severity, d.disaster_status,
                    d.location_address, d.affected_area, d.description,
                    d.people_affected, d.multiple_casualties,
                    d.structural_damage, d.road_blocked,
                    d.assigned_to_id, d.assigned_department, d.assigned_unit_id,
                    d.response_time, d.resolved_time, d.resolution_notes,
                    d.created_by_id, d.disaster_metadata,
                    d.created_at, d.updated_at,
                    ST_Y(d.location::geometry) AS lat,
                    ST_X(d.location::geometry) AS lon
                FROM disasters d
                WHERE {where_clause}
                ORDER BY
                    CASE d.severity
                        WHEN CAST('CRITICAL' AS disaster_severity) THEN 1
                        WHEN CAST('HIGH' AS disaster_severity) THEN 2
                        WHEN CAST('MEDIUM' AS disaster_severity) THEN 3
                        ELSE 4
                    END,
                    d.created_at DESC
            """)

            result = await self.db.execute(sql, params)
            rows = result.mappings().all()

            disaster_list = [self._row_to_dict(row) for row in rows]

            logger.info(f"Found {len(disaster_list)} active disasters in bounds")
            return disaster_list
            
        except Exception as e:
            logger.error(f"Error querying disasters: {e}")
            raise
    
    async def get_disasters_near_point(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Find VERIFIED disasters near a specific point within a radius.
        
        Uses PostGIS distance calculation for efficient proximity search.
        
        Args:
            lat: Latitude of the point
            lon: Longitude of the point
            radius_km: Search radius in kilometers
            limit: Maximum number of results
            
        Returns:
            List of disasters ordered by distance (closest first)
        """
        logger.info(f"Querying disasters near point ({lat}, {lon}) within {radius_km}km")
        
        try:
            # Create point geometry (lon, lat order for PostGIS)
            point = ST_Point(lon, lat)
            
            # Convert km to meters for ST_DWithin
            radius_meters = radius_km * 1000
            
            query = select(Disaster).where(
                and_(
                    Disaster.disaster_status == DisasterStatus.ACTIVE,
                    ST_DWithin(Disaster.location, point, radius_meters, use_spheroid=True)
                )
            )
            
            # Order by distance (closest first)
            query = query.order_by(
                ST_Distance(Disaster.location, point, use_spheroid=True)
            ).limit(limit)
            
            result = await self.db.execute(query)
            disasters = result.scalars().all()
            
            disaster_list = []
            for disaster in disasters:
                disaster_dict = await self._disaster_to_dict(disaster)
                # Add distance in km
                disaster_dict["distance_km"] = await self._calculate_distance(
                    disaster.location, lat, lon
                )
                disaster_list.append(disaster_dict)
            
            logger.info(f"Found {len(disaster_list)} disasters within {radius_km}km")
            return disaster_list
            
        except Exception as e:
            logger.error(f"Error querying disasters near point: {e}")
            raise
    
    async def get_disaster_by_id(
        self,
        disaster_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a single disaster by ID.
        
        Args:
            disaster_id: UUID of the disaster
            
        Returns:
            Disaster dict or None if not found
        """
        try:
            query = select(Disaster).where(Disaster.id == disaster_id)
            result = await self.db.execute(query)
            disaster = result.scalar_one_or_none()
            
            if disaster:
                return await self._disaster_to_dict(disaster)
            return None
            
        except Exception as e:
            logger.error(f"Error querying disaster by ID: {e}")
            raise
    
    async def get_disaster_by_tracking_id(
        self,
        tracking_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a disaster by its tracking ID.
        
        Args:
            tracking_id: Tracking ID (e.g., "DIS-2026-001234")
            
        Returns:
            Disaster dict or None if not found
        """
        try:
            query = select(Disaster).where(Disaster.tracking_id == tracking_id)
            result = await self.db.execute(query)
            disaster = result.scalar_one_or_none()
            
            if disaster:
                return await self._disaster_to_dict(disaster)
            return None
            
        except Exception as e:
            logger.error(f"Error querying disaster by tracking ID: {e}")
            raise
    
    async def create_disaster(
        self,
        disaster: Disaster
    ) -> Disaster:
        """
        Create a new verified disaster record.
        
        Args:
            disaster: Disaster object to create
            
        Returns:
            Created disaster object
        """
        try:
            self.db.add(disaster)
            await self.db.flush()
            await self.db.refresh(disaster)
            return disaster
        except Exception as e:
            logger.error(f"Error creating disaster: {e}")
            raise
    
    async def get_active_disaster_near(
        self,
        lat: float,
        lon: float,
        disaster_type: str,
        radius_km: float = 2.0,
    ) -> Optional[str]:
        """
        Return the ID of the nearest active disaster of the same type within radius_km.

        Used by the evaluation service to find the original disaster when a DUPLICATE
        flag is raised, enabling updateDisasterConfidence to be called on it.

        Args:
            lat: Latitude of the duplicate report
            lon: Longitude of the duplicate report
            disaster_type: Lowercase disaster type value (e.g. "fire")
            radius_km: Search radius in kilometres (default 2km)

        Returns:
            Disaster ID string if an active match is found, None otherwise.
        """
        try:
            dtype_enum = DisasterType(disaster_type)
        except ValueError:
            logger.warning("get_active_disaster_near: unknown disaster_type %r", disaster_type)
            return None

        point = ST_Point(lon, lat)
        radius_m = radius_km * 1000

        query = (
            select(Disaster)
            .where(
                and_(
                    Disaster.disaster_status == DisasterStatus.ACTIVE,
                    Disaster.type == dtype_enum,
                    ST_DWithin(Disaster.location, point, radius_m, use_spheroid=True),
                )
            )
            .order_by(ST_Distance(Disaster.location, point, use_spheroid=True))
            .limit(1)
        )

        try:
            result = await self.db.execute(query)
            disaster = result.scalar_one_or_none()
            return str(disaster.id) if disaster else None
        except Exception as e:
            logger.error("Error in get_active_disaster_near: %s", e)
            return None

    async def update_confidence(
        self,
        disaster_id: str,
        confidence_boost: float = 0.05,
        new_severity: Optional[str] = None,
    ) -> None:
        """
        Increase the confidence score of a disaster's evaluation metadata.

        Called when a DUPLICATE/CORROBORATED/ESCALATED report confirms an existing
        disaster (updateDisasterConfidence spec function).
        Confidence is capped at 0.99; a corroboration_count counter is incremented.
        If new_severity is provided (ESCALATED case), the disaster severity is upgraded.

        Args:
            disaster_id: UUID of the disaster to update
            confidence_boost: Amount to add to existing confidence (default 0.05)
            new_severity: Optional lowercase severity to upgrade to (e.g. "high")
        """
        try:
            result = await self.db.execute(
                select(Disaster).where(Disaster.id == disaster_id)
            )
            disaster = result.scalar_one_or_none()
            if not disaster:
                logger.warning("update_confidence: disaster %s not found", disaster_id)
                return

            meta = dict(disaster.disaster_metadata or {})
            eval_meta = dict(meta.get("evaluation", {}))
            current = float(eval_meta.get("confidence", 0.7))
            eval_meta["confidence"] = min(0.99, round(current + confidence_boost, 4))
            eval_meta["corroboration_count"] = eval_meta.get("corroboration_count", 0) + 1
            meta["evaluation"] = eval_meta
            disaster.disaster_metadata = meta
            flag_modified(disaster, "disaster_metadata")

            if new_severity:
                try:
                    disaster.severity = DisasterSeverity(new_severity.lower())
                except ValueError:
                    logger.warning("update_confidence: unknown severity %r", new_severity)

            await self.db.flush()

            logger.info(
                "Disaster %s confidence updated: %.2f → %.2f (corroboration_count=%d%s)",
                disaster_id,
                current,
                eval_meta["confidence"],
                eval_meta["corroboration_count"],
                f", severity→{new_severity}" if new_severity else "",
            )
        except Exception as e:
            logger.error("Error in update_confidence for disaster %s: %s", disaster_id, e)

    async def set_disaster_status(
        self,
        disaster_id: str,
        status: DisasterStatus,
    ) -> None:
        """
        Update the status of a disaster record.

        Used by the evaluation service to archive a disaster that is subsequently
        determined to be a false alarm (removing it from the live map).

        Args:
            disaster_id: UUID of the disaster to update
            status: New DisasterStatus value
        """
        try:
            stmt = (
                sa_update(Disaster)
                .where(Disaster.id == disaster_id)
                .values(disaster_status=status)
            )
            await self.db.execute(stmt)
            await self.db.flush()
            logger.info("Disaster %s status set to %s", disaster_id, status.value)
        except Exception as e:
            logger.error("Error in set_disaster_status for disaster %s: %s", disaster_id, e)

    async def update_evaluation_metadata(
        self,
        disaster_id: str,
        eval_meta: dict,
        new_severity: Optional[str] = None,
    ) -> None:
        """
        Replace the evaluation section of disaster_metadata with a fresh result,
        appending the prior evaluation to evaluation_history for a full audit trail.

        Args:
            disaster_id: UUID of the disaster to update
            eval_meta: New evaluation dict (confidence, flag, triggers, etc.)
            new_severity: Optional lowercase severity to set on the record
        """
        try:
            result = await self.db.execute(
                select(Disaster).where(Disaster.id == disaster_id)
            )
            disaster = result.scalar_one_or_none()
            if not disaster:
                logger.warning("update_evaluation_metadata: disaster %s not found", disaster_id)
                return

            meta = dict(disaster.disaster_metadata or {})

            # Append prior evaluation to history before overwriting
            prior = meta.get("evaluation")
            if prior:
                history = list(meta.get("evaluation_history", []))
                history.append(prior)
                meta["evaluation_history"] = history

            meta["evaluation"] = eval_meta
            disaster.disaster_metadata = meta
            flag_modified(disaster, "disaster_metadata")

            if new_severity:
                try:
                    disaster.severity = DisasterSeverity(new_severity.lower())
                except ValueError:
                    logger.warning("update_evaluation_metadata: unknown severity %r", new_severity)

            await self.db.flush()
            logger.info("Evaluation metadata updated for disaster %s", disaster_id)
        except Exception as e:
            logger.error("Error in update_evaluation_metadata for disaster %s: %s", disaster_id, e)

    async def get_historical_outcomes(
        self,
        lat: float,
        lon: float,
        disaster_type: str,
        radius_km: float = 5.0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Return a summary of past disaster outcomes of the same type near a point.

        Queries resolved and archived disasters (i.e. ones with a known outcome)
        to build a historical credibility signal for the evaluation engine.

        Returns:
            Dict with keys:
                total           — number of past incidents found
                verified_count  — how many were verified (not false alarms)
                false_alarm_count
                false_alarm_rate — float [0.0, 1.0]
                avg_confidence  — average evaluation confidence of verified ones
                source          — "historical_db"
        """
        try:
            dtype_enum = DisasterType(disaster_type)
        except ValueError:
            return self._empty_historical_context()

        point = ST_Point(lon, lat)
        radius_m = radius_km * 1000

        query = (
            select(Disaster)
            .where(
                and_(
                    Disaster.type == dtype_enum,
                    Disaster.disaster_status.in_([
                        DisasterStatus.RESOLVED,
                        DisasterStatus.ARCHIVED,
                    ]),
                    ST_DWithin(Disaster.location, point, radius_m, use_spheroid=True),
                )
            )
            .limit(limit)
        )

        try:
            result = await self.db.execute(query)
            disasters = result.scalars().all()
        except Exception as e:
            logger.warning("get_historical_outcomes query failed: %s", e)
            return self._empty_historical_context()

        if not disasters:
            return self._empty_historical_context()

        total = len(disasters)
        false_alarm_count = 0
        confidence_sum = 0.0
        verified_count = 0

        for d in disasters:
            flag = (d.disaster_metadata or {}).get("evaluation", {}).get("flag", "")
            confidence = (d.disaster_metadata or {}).get("evaluation", {}).get("confidence", 0.0)
            if flag == "FALSE_ALARM":
                false_alarm_count += 1
            elif d.disaster_status == DisasterStatus.RESOLVED:
                # Only RESOLVED disasters are confirmed real — ARCHIVED without a
                # FALSE_ALARM flag has an unknown outcome and is excluded from
                # both counts to avoid inflating the verified rate.
                verified_count += 1
                confidence_sum += float(confidence)

        # false_alarm_rate is calculated against total so archived unknowns
        # still dilute it slightly, reflecting genuine uncertainty.
        avg_confidence = round(confidence_sum / verified_count, 4) if verified_count else 0.0
        false_alarm_rate = round(false_alarm_count / total, 4)

        return {
            "total": total,
            "verified_count": verified_count,
            "false_alarm_count": false_alarm_count,
            "false_alarm_rate": false_alarm_rate,
            "avg_confidence": avg_confidence,
            "source": "historical_db",
        }

    @staticmethod
    def _empty_historical_context() -> Dict[str, Any]:
        return {
            "total": 0,
            "verified_count": 0,
            "false_alarm_count": 0,
            "false_alarm_rate": 0.0,
            "avg_confidence": 0.0,
            "source": "historical_db",
        }

    async def apply_ert_review(
        self,
        disaster_id: str,
        approved: bool,
        reviewed_by_id: str,
        notes: Optional[str],
        reviewed_at: datetime,
    ) -> bool:
        """
        Record an ERT approval or rejection decision on a disaster.

        Approved  → flag set to NORMAL, trigger_deploy forced True, review
                    metadata stamped into disaster_metadata["evaluation"].
        Rejected  → disaster_status set to ARCHIVED, flag set to FALSE_ALARM.

        Returns True if the disaster was found and updated, False otherwise.
        """
        try:
            result = await self.db.execute(
                select(Disaster).where(Disaster.id == disaster_id)
            )
            disaster = result.scalar_one_or_none()
            if not disaster:
                return False

            meta = dict(disaster.disaster_metadata or {})

            # Snapshot current evaluation into history before applying review
            prior = meta.get("evaluation")
            if prior:
                history = list(meta.get("evaluation_history", []))
                history.append(prior)
                meta["evaluation_history"] = history

            eval_meta = dict(meta.get("evaluation", {}))

            eval_meta["reviewed_by_id"] = reviewed_by_id
            eval_meta["reviewed_at"] = reviewed_at.isoformat()
            eval_meta["review_notes"] = notes

            if approved:
                eval_meta["flag"] = "NORMAL"
                eval_meta["trigger_deploy"] = True
                disaster.disaster_status = DisasterStatus.ACTIVE
            else:
                eval_meta["flag"] = "FALSE_ALARM"
                disaster.disaster_status = DisasterStatus.ARCHIVED

            meta["evaluation"] = eval_meta
            disaster.disaster_metadata = meta
            flag_modified(disaster, "disaster_metadata")

            await self.db.flush()
            logger.info(
                "ERT review applied to disaster %s: approved=%s reviewed_by=%s",
                disaster_id,
                approved,
                reviewed_by_id,
            )
            return True
        except Exception as e:
            logger.error("Error in apply_ert_review for disaster %s: %s", disaster_id, e)
            raise

    async def get_all_active_disasters(self) -> List[Dict[str, Any]]:
        """
        Return all ACTIVE disasters, ordered by severity (critical first) then newest first.

        Used by the active-ranked endpoint (Alternative Flow 1: Multiple Concurrent Disasters).
        Unlike list_active_disasters, this has no spatial bounds — it returns everything active.
        """
        try:
            query = (
                select(Disaster)
                .where(Disaster.disaster_status == DisasterStatus.ACTIVE)
                .order_by(Disaster.severity.desc(), Disaster.created_at.desc())
            )
            result = await self.db.execute(query)
            disasters = result.scalars().all()
            return [await self._disaster_to_dict(d) for d in disasters]
        except Exception as e:
            logger.error("Error in get_all_active_disasters: %s", e)
            raise

    async def update_disaster_fields_if_higher(
        self,
        disaster_id: str,
        people_affected: int,
        multiple_casualties: bool,
        structural_damage: bool,
        road_blocked: bool,
    ) -> None:
        """
        Update a disaster's impact fields when a duplicate report carries higher values.

        Each field is only updated when the new value is strictly worse (higher):
        - people_affected: take the maximum
        - multiple_casualties / structural_damage / road_blocked: OR (True wins)

        Called during the DUPLICATE branch of _persist_result (Alternative Flow 2).
        """
        try:
            result = await self.db.execute(
                select(Disaster).where(Disaster.id == disaster_id)
            )
            disaster = result.scalar_one_or_none()
            if not disaster:
                logger.warning("update_disaster_fields_if_higher: disaster %s not found", disaster_id)
                return

            changed = False
            if people_affected > (disaster.people_affected or 0):
                disaster.people_affected = people_affected
                changed = True
            if multiple_casualties and not disaster.multiple_casualties:
                disaster.multiple_casualties = True
                changed = True
            if structural_damage and not disaster.structural_damage:
                disaster.structural_damage = True
                changed = True
            if road_blocked and not disaster.road_blocked:
                disaster.road_blocked = True
                changed = True

            if changed:
                await self.db.flush()
                logger.info(
                    "Updated impact fields for disaster %s from duplicate report "
                    "(people=%d, casualties=%s, structural=%s, road=%s)",
                    disaster_id, people_affected, multiple_casualties,
                    structural_damage, road_blocked,
                )
        except Exception as e:
            logger.error(
                "Error in update_disaster_fields_if_higher for disaster %s: %s",
                disaster_id, e,
            )

    async def update_disaster(
        self,
        disaster: Disaster
    ) -> Disaster:
        """
        Update an existing disaster record.
        
        Args:
            disaster: Disaster object with updates
            
        Returns:
            Updated disaster object
        """
        try:
            await self.db.flush()
            await self.db.refresh(disaster)
            return disaster
        except Exception as e:
            logger.error(f"Error updating disaster: {e}")
            raise
    
    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert a raw SQL row (from text() queries) to a dictionary."""
        return {
            "id": str(row["id"]),
            "tracking_id": row["tracking_id"],
            "type": str(row["type"]),
            "severity": str(row["severity"]),
            "status": str(row["disaster_status"]),
            "location": {
                "lat": float(row["lat"]) if row["lat"] else None,
                "lon": float(row["lon"]) if row["lon"] else None,
            },
            "location_address": row["location_address"],
            "affected_area": row["affected_area"],
            "description": row["description"],
            "people_affected": row["people_affected"],
            "multiple_casualties": row["multiple_casualties"],
            "structural_damage": row["structural_damage"],
            "road_blocked": row["road_blocked"],
            "assigned_to_id": str(row["assigned_to_id"]) if row["assigned_to_id"] else None,
            "assigned_department": str(row["assigned_department"]) if row["assigned_department"] else None,
            "assigned_unit_id": str(row["assigned_unit_id"]) if row["assigned_unit_id"] else None,
            "response_time": row["response_time"].isoformat() if row["response_time"] else None,
            "resolved_time": row["resolved_time"].isoformat() if row["resolved_time"] else None,
            "resolution_notes": row["resolution_notes"],
            "created_by_id": str(row["created_by_id"]) if row["created_by_id"] else None,
            "disaster_metadata": row["disaster_metadata"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "report_count": 0,
            "report_status": "verified",
            "is_user_reported": False,
        }

    async def _disaster_to_dict(self, disaster: Disaster) -> Dict[str, Any]:
        """
        Convert a Disaster ORM object to a dictionary.
        
        Handles PostGIS geometry serialization to GeoJSON.
        
        Args:
            disaster: Disaster ORM object
            
        Returns:
            Dictionary with all disaster fields
        """
        # Get geometry as GeoJSON
        location_geojson = await self.db.scalar(
            select(ST_AsGeoJSON(disaster.location))
        )
        
        location_data = json.loads(location_geojson) if location_geojson else None
        
        # Extract lat/lon from GeoJSON
        lat, lon = None, None
        if location_data and location_data.get("coordinates"):
            lon, lat = location_data["coordinates"]
        
        # Alternatively, extract directly from geometry
        if not (lat and lon):
            lon = await self.db.scalar(select(ST_X(disaster.location.ST_Transform(4326))))
            lat = await self.db.scalar(select(ST_Y(disaster.location.ST_Transform(4326))))
        
        return {
            "id": str(disaster.id),
            "tracking_id": disaster.tracking_id,
            "type": disaster.type.value if hasattr(disaster.type, 'value') else str(disaster.type),
            "severity": disaster.severity.value if hasattr(disaster.severity, 'value') else str(disaster.severity),
            "status": disaster.disaster_status.value if hasattr(disaster.disaster_status, 'value') else str(disaster.disaster_status),
            "location": {
                "lat": lat,
                "lon": lon,
                "geojson": location_data
            },
            "location_address": disaster.location_address,
            "affected_area": disaster.affected_area,
            "description": disaster.description,
            "created_at": disaster.created_at.isoformat() if disaster.created_at else None,
            "updated_at": disaster.updated_at.isoformat() if disaster.updated_at else None,
            # Impact assessment
            "people_affected": disaster.people_affected,
            "multiple_casualties": disaster.multiple_casualties,
            "structural_damage": disaster.structural_damage,
            "road_blocked": disaster.road_blocked,
            # Assignment
            "assigned_to_id": str(disaster.assigned_to_id) if disaster.assigned_to_id else None,
            "assigned_department": disaster.assigned_department.value if disaster.assigned_department else None,
            # Timeline
            "response_time": disaster.response_time.isoformat() if disaster.response_time else None,
            "resolved_time": disaster.resolved_time.isoformat() if disaster.resolved_time else None,
            "resolution_notes": disaster.resolution_notes,
            # Creation info
            "created_by_id": str(disaster.created_by_id) if disaster.created_by_id else None,
            # Metadata
            "disaster_metadata": disaster.disaster_metadata,
            # Report count (if reports relationship is loaded)
            "report_count": len(disaster.reports) if hasattr(disaster, "reports") and disaster.reports else 0,
        }
    
    async def _calculate_distance(
        self,
        location_geom,
        lat: float,
        lon: float
    ) -> float:
        """
        Calculate distance between disaster location and a point in kilometers.
        
        Args:
            location_geom: PostGIS geometry of disaster location
            lat: Target latitude
            lon: Target longitude
            
        Returns:
            Distance in kilometers
        """
        try:
            # Create point for target location
            point = ST_Point(lon, lat)
            
            # Calculate distance in meters using spheroid
            distance_meters = await self.db.scalar(
                select(ST_Distance(location_geom, point, use_spheroid=True))
            )
            
            # Convert meters to km
            distance_km = distance_meters / 1000.0 if distance_meters else 0.0
            
            return round(distance_km, 2)
            
        except Exception as e:
            logger.warning(f"Error calculating distance: {e}")
            return 0.0