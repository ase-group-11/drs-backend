from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import case

from app.db.models import Disaster
from app.repositories.base_repository import BaseRepository

class DisasterRepository(BaseRepository[Disaster]):
    """
    Repository for Disaster-specific database operations.
    Extends BaseRepository with geographic and business logic queries.
    """

    def __init__(self, db: Session):
        super().__init__(Disaster, db)

    def create_disaster(
        self,
        location_lat: float,
        location_lng: float,
        disaster_type: str,
        severity: str,
        description: str,
        reporter_id: str,
        image_urls: Optional[List[str]] = None
    ) -> Disaster:
        """
        Create a new disaster report with PostGIS point geometry.

        Args:
            location_lat: Latitude coordinate
            location_lng: Longitude coordinate
            disaster_type: Type of disaster (enum value)
            severity: Severity level (enum value)
            description: Detailed description
            reporter_id: ID of reporting user
            image_urls: Optional list of image URLs

        Returns:
            Created Disaster object
        """
        # Create PostGIS point using WKT (Well-Known Text)
        # Note: PostGIS expects (longitude, latitude) order
        point_wkt = f'POINT({location_lng} {location_lat})'

        # Handle None default for image_urls
        if image_urls is None:
            image_urls = []

        disaster_data = {
            'location': point_wkt,
            'location_lat': location_lat,
            'location_lng': location_lng,
            'disaster_type': disaster_type,
            'severity': severity,
            'description': description,
            'reporter_id': reporter_id,
            'image_urls': image_urls,
        }

        return self.create(disaster_data)

    def get_by_reporter(self, reporter_id: str) -> List[Disaster]:
        """Get all disasters reported by a specific user"""
        return self.db.query(Disaster).filter(
            Disaster.reporter_id == reporter_id
        ).order_by(Disaster.created_at.desc()).all()

    def get_by_severity(self, severity: str) -> List[Disaster]:
        """Get all disasters of a specific severity level"""
        return self.db.query(Disaster).filter(
            Disaster.severity == severity
        ).order_by(Disaster.created_at.desc()).all()

    def get_by_status(self, status: str) -> List[Disaster]:
        """Get all disasters with a specific status"""
        return self.db.query(Disaster).filter(
            Disaster.status == status
        ).order_by(Disaster.created_at.desc()).all()

    def get_pending_disasters(self) -> List[Disaster]:
        """Get all pending disasters (for ERT assignment), ordered by severity priority"""
        # Use CASE expression to map severity to numeric priority for correct sorting
        severity_order = case(
            (Disaster.severity == 'critical', 4),
            (Disaster.severity == 'high', 3),
            (Disaster.severity == 'medium', 2),
            (Disaster.severity == 'low', 1),
            else_=0
        )

        return self.db.query(Disaster).filter(
            Disaster.status == 'pending'
        ).order_by(
            severity_order.desc(),      # Critical first (4 → 3 → 2 → 1)
            Disaster.created_at.asc()   # Oldest first
        ).all()
