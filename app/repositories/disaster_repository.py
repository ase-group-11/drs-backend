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
from sqlalchemy import select, and_, or_, func, text
from sqlalchemy.ext.asyncio import AsyncSession
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