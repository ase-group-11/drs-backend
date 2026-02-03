"""
STUB FILE - To be implemented by Developer 2

This file defines the interface contract for ERT notification services.
Developer 1 has created this stub to document the expected API.
"""

from typing import List
from sqlalchemy.orm import Session

class ERTNotificationService:
    """
    Service for notifying Emergency Response Teams about new disasters.

    IMPLEMENTATION REQUIRED BY DEVELOPER 2
    """

    def __init__(self, db: Session):
        self.db = db

    def notify_nearby_ert(
        self,
        disaster_id: int,
        location_lat: float,
        location_lng: float,
        severity: str
    ) -> List[int]:
        """
        Notify ERT teams near a disaster location.

        Args:
            disaster_id: ID of the disaster report
            location_lat: Latitude of disaster
            location_lng: Longitude of disaster
            severity: Severity level (affects notification radius)

        Returns:
            List of user_ids of notified ERT members

        TODO (Developer 2):
        - Query users with role='ert' within radius
        - Calculate radius based on severity:
          * critical: 10km
          * high: 5km
          * medium: 3km
          * low: 1km
        - Use PostGIS ST_DWithin for geographic query
        - Send push notifications / SMS via Twilio
        - Log notification events to audit log
        - Return list of notified user IDs

        Example PostGIS Query:
            from geoalchemy2 import func as geo_func

            ert_users = db.query(User).filter(
                User.role == UserRole.ERT.value,
                geo_func.ST_DWithin(
                    geo_func.ST_MakePoint(User.location_lng, User.location_lat),
                    geo_func.ST_MakePoint(location_lng, location_lat),
                    radius_km * 1000  # Convert to meters
                )
            ).all()
        """
        raise NotImplementedError("To be implemented by Developer 2")
