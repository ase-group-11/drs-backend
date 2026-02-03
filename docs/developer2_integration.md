# Developer 2 Integration Guide

## Overview
Developer 1 has completed the disaster reporting foundation. This document outlines integration points for Developer 2's AI and notification features.

## Completed by Developer 1

### Database Schema
- `disasters` table with PostGIS support (Geography POINT column)
- `users.role` field (citizen/ert/admin)
- Geographic indexing on lat/lng columns
- ARRAY column for image URLs
- All necessary enums: DisasterType, DisasterSeverity, DisasterStatus

### API Endpoint
- `POST /api/v1/disasters/report` - Fully functional with stubs for your features
- Authentication via Bearer token in Authorization header
- Automatic validation of Dublin bounds (lat 53.2-53.5, lng -6.5 to -6.0)
- ERT role detection built-in

### Repository Layer
- `DisasterRepository` - All CRUD operations implemented
- `get_pending_disasters()` - Pre-built for ERT assignment (ordered by severity DESC, created_at ASC)
- `get_by_reporter()`, `get_by_severity()`, `get_by_status()` - Query helpers

### Authentication
- `get_current_user()` dependency - Use in your endpoints to get authenticated user
- Session token system via Redis (format: `session:<token>` → `user_id`)

### Audit Logging
- `log_event()` function in `app/core/audit.py`
- Dedicated audit log at `logs/audit.log`
- JSON-formatted events for easy parsing

---

## Your Integration Tasks

### 1. ERT Notification Service

**File**: `app/services/notification_service.py` (stub exists)

**Requirements**:
- Implement `notify_nearby_ert()` method
- Use PostGIS query to find ERTs within radius
- Severity-based radius:
  - `critical`: 10km
  - `high`: 5km
  - `medium`: 3km
  - `low`: 1km
- Send notifications via Twilio SMS (existing service available)
- Log all notifications to audit log

**Example PostGIS Query**:
```python
from geoalchemy2 import func as geo_func
from app.models import User, UserRole

# Calculate radius based on severity
radius_km = {
    'critical': 10,
    'high': 5,
    'medium': 3,
    'low': 1
}.get(severity, 3)

# Query ERTs within radius (PostGIS ST_DWithin uses meters)
ert_users = db.query(User).filter(
    User.role == UserRole.ERT.value,
    geo_func.ST_DWithin(
        geo_func.ST_MakePoint(User.location_lng, User.location_lat),
        geo_func.ST_MakePoint(location_lng, location_lat),
        radius_km * 1000  # Convert to meters
    )
).all()

# Send notifications
for ert in ert_users:
    TwilioService.send_sms(
        to_number=ert.mobile_number,
        message=f"New {severity} disaster reported at ({location_lat}, {location_lng})"
    )
```

**Integration Point**:
Uncomment lines 89-99 in `app/api/v1/disasters.py`

---

### 2. AI Verification Service

**File**: `app/services/ai_verification_service.py` (stub exists)

**Requirements**:
- Implement `verify_severity()` method
- Use OpenAI Vision API for image analysis
- Compare AI recommendation with user-reported severity
- Update disaster record if discrepancy > 1 level
- Log all verifications

**Example Implementation**:
```python
import openai

def verify_severity(self, disaster_id: int, images: List[str], description: str) -> str:
    # Analyze images with OpenAI Vision
    response = openai.ChatCompletion.create(
        model="gpt-4-vision-preview",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Analyze this disaster and rate severity (low/medium/high/critical): {description}"
                },
                *[{"type": "image_url", "image_url": url} for url in images[:3]]
            ]
        }]
    )

    # Extract severity from response
    ai_severity = extract_severity_from_response(response)

    # Log verification
    log_event(
        event_type='ai_verification',
        user_id=None,
        details={
            'disaster_id': disaster_id,
            'ai_severity': ai_severity
        }
    )

    return ai_severity
```

**Integration Point**:
Uncomment lines 89-99 in `app/api/v1/disasters.py`

---

### 3. Testing Your Features

**Test Files to Create**:
- `app/tests/unit/test_notification_service.py`
- `app/tests/unit/test_ai_verification.py`
- `app/tests/integration/test_disaster_workflow.py`

**Test Database**:
For PostGIS testing, update `conftest.py` to use PostgreSQL test database instead of SQLite:

```python
SQLALCHEMY_TEST_DATABASE_URL = "postgresql://user:pass@localhost/test_drs"
```

**Note**: SQLite doesn't support PostGIS, so geographic queries won't work in current tests.

---

## Available Helpers

### Audit Logging
```python
from app.core.audit import log_event

log_event(
    event_type='ert_notified',
    user_id=ert_user.user_id,
    details={
        'disaster_id': disaster_id,
        'notification_method': 'sms',
        'distance_km': 2.5
    },
    ip_address=None
)
```

### Twilio SMS
```python
from app.services.twilio_service import TwilioService

TwilioService.send_sms(
    to_number=ert_user.mobile_number,
    message=f"New disaster reported: {disaster.description[:50]}..."
)
```

### Repository Access
```python
from app.repositories.disaster_repository import DisasterRepository

disaster_repo = DisasterRepository(db)

# Get pending disasters for ERT dashboard
pending = disaster_repo.get_pending_disasters()

# Get disasters by severity
critical_disasters = disaster_repo.get_by_severity('critical')
```

---

## File Structure

```
app/
├── models/
│   ├── user.py (MODIFIED - added role field)
│   └── disaster.py (NEW - your data model)
├── schemas/
│   └── disaster_schemas.py (NEW - validation rules)
├── repositories/
│   ├── base_repository.py (NEW - generic CRUD)
│   └── disaster_repository.py (NEW - disaster operations)
├── api/v1/
│   └── disasters.py (NEW - main endpoint with stubs)
├── services/
│   ├── notification_service.py (STUB - implement this)
│   └── ai_verification_service.py (STUB - implement this)
├── core/
│   └── audit.py (NEW - logging utility)
└── tests/
    ├── conftest.py (NEW - test fixtures)
    └── unit/
        └── test_disaster_api.py (NEW - API tests)
```

---

## Database Migration Required

After you add location fields to the User model for ERT positioning:

```python
# In User model, add:
location_lat: Mapped[float] = mapped_column(Float, nullable=True)
location_lng: Mapped[float] = mapped_column(Float, nullable=True)
```

Then create migration:
```bash
alembic revision --autogenerate -m "Add location to User model"
alembic upgrade head
```

---

## Questions & Support

### Integration Points
Review the stubs in:
- `/app/services/notification_service.py`
- `/app/services/ai_verification_service.py`

### Main Endpoint
Check `/app/api/v1/disasters.py` lines 89-99 for integration comments (TODO markers)

### Example Workflow
1. Citizen reports disaster → endpoint receives request
2. Disaster created in database
3. **[YOUR CODE]** AI verifies severity if high/critical
4. **[YOUR CODE]** Notifications sent to nearby ERTs
5. Response returned with `ert_notified` flag

---

## Testing Checklist

Before integration:
- [ ] Notification service can query ERTs by location
- [ ] AI service can analyze images and return severity
- [ ] Tests pass for notification radius calculations
- [ ] Tests pass for AI verification logic

After integration:
- [ ] End-to-end test: Citizen report triggers notifications
- [ ] End-to-end test: ERT report doesn't trigger notifications
- [ ] Audit log contains notification events
- [ ] Performance test: Response time < 2 seconds

---

## Performance Considerations

### PostGIS Indexes
Automatically created on the `location` Geography column (GIST index).

### Optimization Tips
- Cache ERT locations in Redis for faster queries
- Use background tasks for AI verification (doesn't block response)
- Batch notifications instead of individual SMS calls
- Consider using RabbitMQ for async processing (your responsibility)

---

## Contact

For questions about the disaster reporting foundation, review:
- Plan document: `/Users/tmalomo/.claude/plans/gleaming-watching-crayon.md`
- This guide: `docs/developer2_integration.md`
- Implementation code in `app/` directory

Good luck with your implementation! 🚀
