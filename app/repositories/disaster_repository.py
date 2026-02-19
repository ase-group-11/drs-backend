
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
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.functions import (
    ST_MakeEnvelope, ST_Within, ST_Distance, 
    ST_AsGeoJSON, ST_Point, ST_DWithin, ST_X, ST_Y
)

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
            # Parse bounds
            south, west, north, east = map(float, bounds.split(','))
            
            # Build query with PostGIS spatial filter
            query = select(Disaster).where(
                and_(
                    Disaster.disaster_status == DisasterStatus.ACTIVE,
                    ST_Within(
                        Disaster.location,
                        ST_MakeEnvelope(west, south, east, north, 4326)
                    )
                )
            )
            
            # Apply optional filters
            if disaster_types:
                type_enums = []
                for dtype in disaster_types:
                    try:
                        type_enums.append(DisasterType(dtype.upper()))
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
                    min_severity_enum = DisasterSeverity(min_severity.upper())
                    min_value = severity_order.get(min_severity_enum, 0)
                    
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
            
            # Order by severity (critical first) then creation time (newest first)
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