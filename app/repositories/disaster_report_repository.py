# File: app/repositories/disaster_report_repository.py
"""
Disaster Report Repository - Database access layer for user-submitted reports.

Handles UNVERIFIED user submissions before they become official disasters.
"""

import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.functions import ST_AsGeoJSON, ST_X, ST_Y, ST_DWithin, ST_MakePoint

from app.db.models.disaster_report import DisasterReport
from app.db.models.enums import DisasterReportStatus, DisasterType, DisasterSeverity

logger = logging.getLogger(__name__)


class DisasterReportRepository:
    """
    Repository for accessing user-submitted disaster reports.
    
    These are UNVERIFIED reports submitted by users.
    After verification by emergency teams, they become official Disasters.
    """
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
    
    async def get_pending_reports(
        self,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get user-submitted reports pending verification.
        
        Used by emergency team dashboard to review and verify/reject reports.
        
        Args:
            limit: Maximum number of pending reports
            
        Returns:
            List of reports pending verification (oldest first - FIFO)
        """
        logger.info(f"Querying pending disaster reports (limit={limit})")
        
        try:
            # Select individual columns instead of the whole model
            query = select(
                DisasterReport.id,
                DisasterReport.user_id,
                DisasterReport.disaster_type,
                DisasterReport.severity,
                DisasterReport.description,
                DisasterReport.location_address,
                DisasterReport.people_affected,
                DisasterReport.multiple_casualties,
                DisasterReport.structural_damage,
                DisasterReport.road_blocked,
                DisasterReport.report_status,
                DisasterReport.disaster_id,
                DisasterReport.reviewed_by_id,
                DisasterReport.reviewed_at,
                DisasterReport.rejection_reason,
                DisasterReport.created_at,
                ST_AsGeoJSON(DisasterReport.location).label('location_geojson')
            ).where(
                DisasterReport.report_status == DisasterReportStatus.PENDING
            ).order_by(
                DisasterReport.created_at.asc()
            ).limit(limit)
            
            result = await self.db.execute(query)
            reports = result.all()
            
            report_list = []
            for report in reports:
                try:
                    # Parse location with error handling
                    location_geojson = report.location_geojson
                    location_data = None
                    lat, lon = None, None
                    
                    if location_geojson:
                        try:
                            # Strip any whitespace and validate JSON
                            location_geojson_str = str(location_geojson).strip()
                            if location_geojson_str:
                                location_data = json.loads(location_geojson_str)
                                if location_data and location_data.get("coordinates"):
                                    coords = location_data["coordinates"]
                                    if isinstance(coords, list) and len(coords) >= 2:
                                        lon, lat = coords[0], coords[1]
                        except json.JSONDecodeError as je:
                            logger.warning(f"Failed to parse location JSON for report {report.id}: {je}")
                            logger.warning(f"Raw JSON: {repr(location_geojson)}")
                        except Exception as e:
                            logger.warning(f"Error extracting coordinates for report {report.id}: {e}")
                
                    report_dict = {
                        "id": str(report.id),
                        "user_id": str(report.user_id),
                        "disaster_type": report.disaster_type.value, 
                        "severity": report.severity.value, 
                        "description": report.description,
                        "location": {
                            "lat": lat, 
                            "lon": lon, 
                            "geojson": location_data
                        },

                        "location_address": report.location_address,
                        "people_affected": report.people_affected, 
                        "multiple_casualties": report.multiple_casualties, 
                        "structural_damage": report.structural_damage, 
                        "road_blocked": report.road_blocked,
                        "report_status": report.report_status.value, 
                        "disaster_id": str(report.disaster_id) if report.disaster_id else None,
                        "reviewed_by_id": str(report.reviewed_by_id) if report.reviewed_by_id else None,
                        "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
                        "rejection_reason": report.rejection_reason,
                        "created_at": report.created_at.isoformat() if report.created_at else None,
                        "photo_count": 0,
                    }
                    report_list.append(report_dict)
                except Exception as e:
                    logger.error(f"Error processing report row: {e}")
                    logger.error(f"row data: id = {report.id if hasattr(report, 'id') else 'unknown'}")
                    continue
            
            logger.info(f"Found {len(report_list)} pending reports")
            return report_list
            
        except Exception as e:
            logger.error(f"Error querying pending reports: {e}")
            raise
    
    async def get_report_by_id(
        self,
        report_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a report by ID."""
        try:
            query = select(
                DisasterReport,
                ST_AsGeoJSON(DisasterReport.location).label('location_geojson')
            ).where(DisasterReport.id == report_id)
            result = await self.db.execute(query)
            row = result.one_or_none()

            if row:
                report, location_geojson = row
                return self._build_report_dict(report, location_geojson)
            return None
        except Exception as e:
            logger.error(f"Error querying report by ID: {e}")
            raise
    
    async def get_reports_by_user(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get all reports submitted by a specific user."""
        try:
            query = select(DisasterReport).where(
                DisasterReport.user_id == user_id
            ).order_by(
                DisasterReport.created_at.desc()
            ).limit(limit)
            
            result = await self.db.execute(query)
            reports = result.scalars().all()
            
            return [await self._report_to_dict(r) for r in reports]
        except Exception as e:
            logger.error(f"Error querying user reports: {e}")
            raise
    
    async def create_report(
        self,
        report: DisasterReport
    ) -> DisasterReport:
        """Create a new disaster report."""
        try:
            self.db.add(report)
            await self.db.flush()
            await self.db.refresh(report)
            return report
        except Exception as e:
            logger.error(f"Error creating report: {e}")
            raise
    
    async def update_report(
        self,
        report: DisasterReport
    ) -> DisasterReport:
        """Update an existing report."""
        try:
            await self.db.flush()
            await self.db.refresh(report)
            return report
        except Exception as e:
            logger.error(f"Error updating report: {e}")
            raise
    
    async def _report_to_dict(self, report: DisasterReport) -> Dict[str, Any]:
        """Convert DisasterReport to dictionary."""
        # Get geometry as GeoJSON
        location_geojson = await self.db.scalar(
            select(ST_AsGeoJSON(report.location))
        )
        
        location_data = json.loads(location_geojson) if location_geojson else None
        
        # Extract lat/lon
        lat, lon = None, None
        if location_data and location_data.get("coordinates"):
            lon, lat = location_data["coordinates"]
        
        return {
            "id": str(report.id),
            "user_id": str(report.user_id),
            "disaster_type": report.disaster_type.value,
            "severity": report.severity.value,
            "description": report.description,
            "location": {
                "lat": lat,
                "lon": lon,
                "geojson": location_data
            },
            "location_address": report.location_address,
            "people_affected": report.people_affected,
            "multiple_casualties": report.multiple_casualties,
            "structural_damage": report.structural_damage,
            "road_blocked": report.road_blocked,
            "report_status": report.report_status.value,
            "disaster_id": str(report.disaster_id) if report.disaster_id else None,
            "reviewed_by_id": str(report.reviewed_by_id) if report.reviewed_by_id else None,
            "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
            "rejection_reason": report.rejection_reason,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "photo_count": len(report.photos) if hasattr(report, "photos") and report.photos else 0,
            "photo_urls": [p.image_url for p in report.photos] if hasattr(report, "photos") and report.photos else [],
        }

    async def get_recent_reports_near(
        self,
        lat: float,
        lon: float,
        disaster_type: str,
        exclude_report_id: str,
        exclude_user_id: Optional[str] = None,
        radius_km: float = 2.0,
        window_minutes: int = 120,
    ) -> List[Dict[str, Any]]:
        """
        Get recent reports of the same disaster type within a geographic radius.

        Used by the nearby incident check to detect DUPLICATE, ESCALATED,
        and CORROBORATED patterns.

        Args:
            lat: Center latitude
            lon: Center longitude
            disaster_type: Disaster type value to match
            exclude_report_id: The current report — excluded from results
            exclude_user_id: The current reporter — excluded so same-user reports
                             are not counted as independent corroboration
            radius_km: Search radius in kilometres (default 2km)
            window_minutes: How far back to look in minutes (default 2 hours)

        Returns:
            List of nearby report dicts with id, user_id, severity, created_at
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        point = func.ST_SetSRID(ST_MakePoint(lon, lat), 4326).cast("geography")

        conditions = [
            DisasterReport.id != exclude_report_id,
            DisasterReport.disaster_type == disaster_type,
            DisasterReport.created_at >= cutoff,
            ST_DWithin(DisasterReport.location, point, radius_km * 1000),
        ]
        if exclude_user_id is not None:
            conditions.append(DisasterReport.user_id != exclude_user_id)

        query = select(
            DisasterReport.id,
            DisasterReport.user_id,
            DisasterReport.severity,
            DisasterReport.created_at,
        ).where(
            and_(*conditions)
        ).order_by(DisasterReport.created_at.desc())

        try:
            result = await self.db.execute(query)
            rows = result.all()
            return [
                {
                    "id": str(r.id),
                    "user_id": str(r.user_id),
                    "severity": r.severity.value,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error querying nearby reports: {e}")
            return []

    async def get_reports_by_disaster_id(
        self,
        disaster_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Return all reports linked to a specific disaster record.

        Used during corroborating-report aggregation to gather the full
        set of reports that contributed to this disaster so the evaluation
        can be re-run with their combined data.

        Returns a lightweight dict with only the fields needed for aggregation.
        """
        query = select(
            DisasterReport.id,
            DisasterReport.severity,
            DisasterReport.people_affected,
            DisasterReport.multiple_casualties,
            DisasterReport.structural_damage,
            DisasterReport.road_blocked,
        ).where(DisasterReport.disaster_id == disaster_id)

        try:
            result = await self.db.execute(query)
            rows = result.all()
            return [
                {
                    "id": str(r.id),
                    "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                    "people_affected": r.people_affected or 0,
                    "multiple_casualties": r.multiple_casualties or False,
                    "structural_damage": r.structural_damage or False,
                    "road_blocked": r.road_blocked or False,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("get_reports_by_disaster_id error: %s", e)
            return []

    async def update_report_status(
        self,
        report_id: str,
        status: DisasterReportStatus,
        disaster_id: Optional[str] = None,
    ) -> None:
        """
        Update report_status and optionally link to a verified disaster.

        Called by the evaluation service after persisting the result.
        """
        values: Dict[str, Any] = {
            "report_status": status,
            "reviewed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        if disaster_id is not None:
            values["disaster_id"] = disaster_id

        stmt = update(DisasterReport).where(DisasterReport.id == report_id).values(**values)
        await self.db.execute(stmt)

    def _build_report_dict(self, report: DisasterReport, location_geojson: str) -> Dict[str, Any]:
        """Convert DisasterReport ORM object + pre-fetched GeoJSON string to dict."""
        location_data = None
        lat, lon = None, None
        if location_geojson:
            try:
                location_data = json.loads(str(location_geojson))
                if location_data and location_data.get("coordinates"):
                    lon, lat = location_data["coordinates"]
            except (json.JSONDecodeError, Exception):
                pass

        return {
            "id": str(report.id),
            "user_id": str(report.user_id),
            "disaster_type": report.disaster_type.value,
            "severity": report.severity.value,
            "description": report.description,
            "location": {"lat": lat, "lon": lon, "geojson": location_data},
            "location_address": report.location_address,
            "people_affected": report.people_affected,
            "multiple_casualties": report.multiple_casualties,
            "structural_damage": report.structural_damage,
            "road_blocked": report.road_blocked,
            "report_status": report.report_status.value if report.report_status else None,
            "disaster_id": str(report.disaster_id) if report.disaster_id else None,
            "reviewed_by_id": str(report.reviewed_by_id) if report.reviewed_by_id else None,
            "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
            "rejection_reason": report.rejection_reason,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        }