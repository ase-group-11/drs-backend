# File: app/repositories/disaster_report_repository.py
"""
Disaster Report Repository - Database access layer for user-submitted reports.

Handles UNVERIFIED user submissions before they become official disasters.
"""

import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.functions import ST_AsGeoJSON, ST_X, ST_Y

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
            query = select(DisasterReport).where(
                DisasterReport.report_status == DisasterReportStatus.PENDING
            ).order_by(
                DisasterReport.created_at.asc()  # Oldest first (FIFO)
            ).limit(limit)
            
            result = await self.db.execute(query)
            reports = result.scalars().all()
            
            report_list = []
            for report in reports:
                report_dict = await self._report_to_dict(report)
                report_list.append(report_dict)
            
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
            query = select(DisasterReport).where(DisasterReport.id == report_id)
            result = await self.db.execute(query)
            report = result.scalar_one_or_none()
            
            if report:
                return await self._report_to_dict(report)
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
        }