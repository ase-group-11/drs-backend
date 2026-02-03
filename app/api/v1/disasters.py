import logging
from fastapi import APIRouter, Depends, status, Request
from sqlalchemy.orm import Session

from app.schemas.disaster_schemas import DisasterCreate, DisasterResponse
from app.dependencies import get_db, get_current_user
from app.models import User, UserRole
from app.repositories.disaster_repository import DisasterRepository
from app.core.audit import log_event

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/disasters",
    tags=["Disasters"]
)


@router.post("/report", response_model=DisasterResponse, status_code=status.HTTP_201_CREATED)
def report_disaster(
    disaster_data: DisasterCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> DisasterResponse:
    """
    Report a new disaster incident.

    Workflow:
    1. Authenticate user (via get_current_user dependency)
    2. Validate disaster data (via Pydantic schema)
    3. Create disaster record in database
    4. Log audit event
    5. Check if reporter is ERT member
    6. If reporter is NOT ERT:
        - [STUB] Trigger AI severity verification (Developer 2)
        - [STUB] Send notifications to nearby ERT teams (Developer 2)
    7. Return disaster response with notification status

    Auth: Requires valid session token (Bearer token in Authorization header)
    """
    logger.info(
        f"Disaster report received from user {current_user.user_id} "
        f"at ({disaster_data.location_lat}, {disaster_data.location_lng})"
    )

    # Create disaster using repository pattern
    disaster_repo = DisasterRepository(db)

    disaster = disaster_repo.create_disaster(
        location_lat=disaster_data.location_lat,
        location_lng=disaster_data.location_lng,
        disaster_type=disaster_data.disaster_type.value,
        severity=disaster_data.severity.value,
        description=disaster_data.description,
        reporter_id=current_user.user_id,
        image_urls=disaster_data.image_urls or []
    )

    logger.info(f"Disaster {disaster.disaster_id} created successfully")

    # Audit logging
    log_event(
        event_type='disaster_reported',
        user_id=current_user.user_id,
        details={
            'disaster_id': disaster.disaster_id,
            'disaster_type': disaster.disaster_type,
            'severity': disaster.severity,
            'location': f"{disaster.location_lat},{disaster.location_lng}",
            'has_images': len(disaster.image_urls) > 0
        },
        ip_address=request.client.host if request.client else None
    )

    # Check if reporter is ERT member
    is_reporter_ert = current_user.role == UserRole.ERT.value
    ert_notified = False

    if not is_reporter_ert:
        logger.info(f"Reporter is not ERT. Triggering notifications for disaster {disaster.disaster_id}")

        # ========================================
        # STUB: Developer 2 Integration Points
        # ========================================

        # TODO (Developer 2): AI Severity Verification
        # if disaster.severity in ['high', 'critical']:
        #     ai_service = AIVerificationService()
        #     verified_severity = ai_service.verify_severity(
        #         disaster_id=disaster.disaster_id,
        #         images=disaster.image_urls,
        #         description=disaster.description
        #     )
        #     if verified_severity != disaster.severity:
        #         disaster_repo.update_severity(disaster.disaster_id, verified_severity)

        # TODO (Developer 2): ERT Notification Service
        # notification_service = ERTNotificationService(db)
        # notified_teams = notification_service.notify_nearby_ert(
        #     disaster_id=disaster.disaster_id,
        #     location_lat=disaster.location_lat,
        #     location_lng=disaster.location_lng,
        #     severity=disaster.severity
        # )
        # ert_notified = len(notified_teams) > 0

        # Placeholder: Mark as notified for now
        ert_notified = True  # Will be actual value from notification_service

        logger.info(f"[STUB] ERT notification would be sent for disaster {disaster.disaster_id}")
    else:
        logger.info(f"Reporter is ERT member. Skipping notifications.")

    # Prepare response
    response = DisasterResponse.model_validate(disaster)
    response.ert_notified = ert_notified if not is_reporter_ert else None

    return response
