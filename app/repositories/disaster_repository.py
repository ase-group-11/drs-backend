# File: app/repositories/disaster_repository.py
"""
Disaster Repository - Database access layer for disaster data.

COMPLETE IMPLEMENTATION with:
- PostGIS spatial queries for efficient geospatial filtering
- Verification status filtering (security - only verified disasters on public map)
- User-reported disaster support
- Pending verification queries for emergency teams
- Spatial proximity searches
- Database session management

Updated to support:
- Use Case 3: Disaster Reporting (user-submitted disasters)
- Use Case 4: Live Map (real-time disaster visualization)

Requires: PostgreSQL with PostGIS extension
"""

import logging

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2 import Geometry
from geoalchemy2.functions import ST_MakeEnvelope, ST_Within, ST_Distance, ST_AsGeoJSON, ST_Point, ST_DWithin

from app.db.models.disaster import Disaster
from app.db.models.enums import DisasterType, DisasterSeverity, DisasterStatus, DisasterReportStatus

logger = logging.getLogger(__name__)


class DisasterRepository:
    """
    Repository for accessing disaster data with geospatial queries.
    
    Provides methods to query disasters within bounding boxes, near points,
    and with various filters (type, severity, status, verification, time range).
    
    CRITICAL SECURITY: By default, only returns VERIFIED disasters to prevent
    false alarms and unconfirmed incidents from appearing on the public map.
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
        max_age_hours: Optional[int] = None,
        include_unverified: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Query active disasters within a bounding box.
        
        Uses PostGIS spatial queries for efficient geospatial filtering.
        Returns disasters as dictionaries (not ORM objects) for easy JSON serialization.
        
        **IMPORTANT SECURITY:** By default, only shows VERIFIED disasters on the public live map.
        User-reported disasters that haven't been verified by emergency teams are hidden
        to prevent false alarms, spam, and unconfirmed incidents.
        
        Args:
            bounds: Bounding box string "south,west,north,east"
            disaster_types: Optional filter by disaster types (flood, fire, earthquake, etc.)
            min_severity: Optional minimum severity (low, medium, high, critical)
            max_age_hours: Optional filter disasters newer than N hours
            include_unverified: If True, include unverified user reports (admin/team only)
            
        Returns:
            List of disaster dicts with all fields including geometry
            
        Example:
            # Public map - only verified disasters
            disasters = await repo.list_active_disasters(
                bounds="53.30,-6.35,53.40,-6.20",
                disaster_types=["flood", "fire"],
                min_severity="medium"
            )
            
            # Admin view - include unverified reports
            all_reports = await repo.list_active_disasters(
                bounds="53.30,-6.35,53.40,-6.20",
                include_unverified=True
            )
        """
        logger.info(f"Querying active disasters for bounds: {bounds}")
        
        try:
            # Parse bounds
            south, west, north, east = map(float, bounds.split(','))
            
            # Build the query with PostGIS spatial filter
            query = select(Disaster).where(
                and_(
                    Disaster.status == DisasterStatus.ACTIVE,
                    ST_Within(
                        Disaster.location,
                        ST_MakeEnvelope(west, south, east, north, 4326)
                    )
                )
            )
            
            # CRITICAL SECURITY: Only show verified disasters on public map
            if not include_unverified:
                query = query.where(
                    Disaster.report_status == DisasterReportStatus.VERIFIED
                )
                logger.debug("Filtering to VERIFIED disasters only (public map)")
            else:
                logger.debug("Including unverified disasters (admin/team view)")
            
            # Apply optional filters
            if disaster_types:
                # Convert string types to enum types
                type_enums = []
                for dtype in disaster_types:
                    try:
                        type_enums.append(DisasterType(dtype.lower()))
                    except ValueError:
                        logger.warning(f"Invalid disaster type: {dtype}")
                
                if type_enums:
                    query = query.where(Disaster.type.in_(type_enums))
            
            if min_severity:
                # Severity ordering: low < medium < high < critical
                severity_order = {
                    DisasterSeverity.LOW: 0,
                    DisasterSeverity.MEDIUM: 1,
                    DisasterSeverity.HIGH: 2,
                    DisasterSeverity.CRITICAL: 3
                }
                
                try:
                    min_severity_enum = DisasterSeverity(min_severity.lower())
                    min_value = severity_order.get(min_severity_enum, 0)
                    
                    # Filter by severity level
                    severity_filters = []
                    for sev, value in severity_order.items():
                        if value >= min_value:
                            severity_filters.append(Disaster.severity == sev)
                    
                    if severity_filters:
                        query = query.where(or_(*severity_filters))
                except ValueError:
                    logger.warning(f"Invalid severity level: {min_severity}")
            
            if max_age_hours:
                cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
                query = query.where(Disaster.created_at >= cutoff_time)
            
            # Order by severity (critical first) and then by creation time (newest first)
            query = query.order_by(
                Disaster.severity.desc(),
                Disaster.created_at.desc()
            )
            
            # Execute query
            result = await self.db.execute(query)
            disasters = result.scalars().all()
            
            # Convert to dictionaries
            disaster_list = []
            for disaster in disasters:
                disaster_dict = await self._disaster_to_dict(disaster)
                disaster_list.append(disaster_dict)
            
            logger.info(
                f"Found {len(disaster_list)} active disasters in bounds "
                f"(include_unverified={include_unverified})"
            )
            return disaster_list
            
        except Exception as e:
            logger.error(f"Error querying disasters: {e}")
            raise
    
    async def get_disasters_near_point(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10,
        limit: int = 10,
        include_unverified: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Find disasters near a specific point within a radius.
        
        Uses PostGIS distance calculation for efficient proximity search.
        Only returns VERIFIED disasters by default for security.
        
        Args:
            lat: Latitude of the point
            lon: Longitude of the point
            radius_km: Search radius in kilometers
            limit: Maximum number of results
            include_unverified: If True, include unverified reports (admin/team only)
            
        Returns:
            List of disasters ordered by distance (closest first)
        """
        logger.info(f"Querying disasters near point ({lat}, {lon}) within {radius_km}km")
        
        try:
            # Create point geometry (lon, lat order for PostGIS)
            point = ST_Point(lon, lat, type_=Geometry)
            
            # Convert km to degrees (approximate)
            # At equator: 1 degree ≈ 111 km
            radius_degrees = radius_km / 111.0
            
            query = select(Disaster).where(
                and_(
                    Disaster.status == DisasterStatus.ACTIVE,
                    ST_DWithin(Disaster.location, point, radius_degrees)
                )
            )
            
            # CRITICAL SECURITY: Only verified disasters by default
            if not include_unverified:
                query = query.where(
                    Disaster.report_status == DisasterReportStatus.VERIFIED
                )
            
            # Order by distance (closest first)
            query = query.order_by(
                ST_Distance(Disaster.location, point)
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
    
    async def get_pending_verifications(
        self,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get user-reported disasters that are pending verification.
        
        Used by emergency team dashboard to review and verify/reject reports.
        
        **EMERGENCY TEAM ONLY** - Returns unverified user reports.
        
        Args:
            limit: Maximum number of pending reports to return
            
        Returns:
            List of disasters pending verification, ordered by creation time (oldest first)
        """
        logger.info(f"Querying pending disaster verifications (limit={limit})")
        
        try:
            query = select(Disaster).where(
                Disaster.report_status == DisasterReportStatus.PENDING
            ).order_by(
                Disaster.created_at.asc()  # Oldest first (FIFO)
            ).limit(limit)
            
            result = await self.db.execute(query)
            disasters = result.scalars().all()
            
            disaster_list = []
            for disaster in disasters:
                disaster_dict = await self._disaster_to_dict(disaster)
                disaster_list.append(disaster_dict)
            
            logger.info(f"Found {len(disaster_list)} disasters pending verification")
            return disaster_list
            
        except Exception as e:
            logger.error(f"Error querying pending verifications: {e}")
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
        
        Used by users to track their reported disasters.
        
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
    
    async def _disaster_to_dict(self, disaster: Disaster) -> Dict[str, Any]:
        """
        Convert a Disaster ORM object to a dictionary.
        
        Handles PostGIS geometry serialization to GeoJSON.
        Includes new fields for user reporting workflow.
        
        Args:
            disaster: Disaster ORM object
            
        Returns:
            Dictionary with all disaster fields
        """
        # Get geometry as GeoJSON
        location_geojson = await self.db.scalar(
            select(ST_AsGeoJSON(disaster.location))
        )
        
        import json
        location_data = json.loads(location_geojson) if location_geojson else None
        
        # Extract lat/lon from GeoJSON
        lat, lon = None, None
        if location_data and location_data.get("coordinates"):
            lon, lat = location_data["coordinates"]
        
        return {
            "id": str(disaster.id),
            "tracking_id": disaster.tracking_id,
            "type": disaster.type.value if hasattr(disaster.type, 'value') else str(disaster.type),
            "severity": disaster.severity.value if hasattr(disaster.severity, 'value') else str(disaster.severity),
            "status": disaster.status.value if hasattr(disaster.status, 'value') else str(disaster.status),
            "report_status": disaster.report_status.value if hasattr(disaster.report_status, 'value') else str(disaster.report_status),
            "location": {
                "lat": lat,
                "lon": lon,
                "geojson": location_data
            },
            "affected_area": disaster.affected_area,
            "description": disaster.description,
            "created_at": disaster.created_at.isoformat() if disaster.created_at else None,
            "updated_at": disaster.updated_at.isoformat() if disaster.updated_at else None,
            # User reporting information
            "is_user_reported": disaster.is_user_reported,
            "reporter_id": str(disaster.reporter_id) if disaster.reporter_id else None,
            "verified_by_id": str(disaster.verified_by_id) if disaster.verified_by_id else None,
            # Photo count (if photos relationship is loaded)
            "photo_count": len(disaster.photos) if hasattr(disaster, "photos") and disaster.photos else 0,
            # Optional metadata
            "metadata": disaster.metadata if hasattr(disaster, "metadata") else None,
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
            point = ST_Point(lon, lat, type_=Geometry)
            
            # Calculate distance in degrees
            distance_degrees = await self.db.scalar(
                select(ST_Distance(location_geom, point))
            )
            
            # Convert degrees to km (approximate)
            # At equator: 1 degree ≈ 111 km
            distance_km = distance_degrees * 111.0
            
            return round(distance_km, 2)
            
        except Exception as e:
            logger.warning(f"Error calculating distance: {e}")
            return 0.0


