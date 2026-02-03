# API Testing Guide - Disaster Reporting System

This guide shows how to test the disaster reporting API endpoints using curl, Postman, or Python.

---

## Prerequisites

1. Application is running: `uvicorn main:app --reload`
2. Database migrations applied (see `docs/database_setup.md`)
3. Redis server running
4. PostgreSQL with PostGIS enabled

---

## Step 1: User Registration & Authentication

### 1.1 Request OTP

```bash
curl -X POST "http://localhost:8000/api/v1/auth/signup/request-otp" \
  -H "Content-Type: application/json" \
  -d '{
    "mobile_number": "+919876543210"
  }'
```

**Expected Response** (200 OK):
```json
{
  "message": "OTP sent successfully. Please verify to complete registration."
}
```

**Note**: Check your Twilio logs or SMS inbox for the OTP code (6 digits).

---

### 1.2 Verify OTP and Get Session Token

```bash
curl -X POST "http://localhost:8000/api/v1/auth/signup/verify" \
  -H "Content-Type: application/json" \
  -d '{
    "mobile_number": "+919876543210",
    "otp_code": "123456"
  }'
```

**Expected Response** (201 Created):
```json
{
  "user_id": 1,
  "mobile_number": "+919876543210",
  "role": "citizen",
  "is_verified": true,
  "created_at": "2026-02-02T10:30:00Z",
  "session_token": "abc123xyz789..."
}
```

**Important**: Save the `session_token` for authenticated requests!

---

## Step 2: Report a Disaster (Authenticated)

### 2.1 Successful Disaster Report

```bash
# Replace YOUR_SESSION_TOKEN with the token from Step 1.2
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.3498,
    "location_lng": -6.2603,
    "disaster_type": "fire",
    "severity": "high",
    "description": "Large fire at city center building with significant smoke",
    "image_urls": [
      "https://example.com/fire1.jpg",
      "https://example.com/fire2.jpg"
    ]
  }'
```

**Expected Response** (201 Created):
```json
{
  "disaster_id": 1,
  "location_lat": 53.3498,
  "location_lng": -6.2603,
  "disaster_type": "fire",
  "severity": "high",
  "description": "Large fire at city center building with significant smoke",
  "reporter_id": 1,
  "status": "pending",
  "image_urls": [
    "https://example.com/fire1.jpg",
    "https://example.com/fire2.jpg"
  ],
  "created_at": "2026-02-02T10:35:00Z",
  "ert_notified": true
}
```

**Note**: `ert_notified: true` indicates ERT notification stubs were triggered (for citizen reporters).

---

### 2.2 Check Audit Log

```bash
# View audit log
tail -f logs/audit.log
```

**Expected Entry**:
```json
{
  "event_type": "disaster_reported",
  "user_id": 1,
  "timestamp": "2026-02-02T10:35:00.123456",
  "details": {
    "disaster_id": 1,
    "disaster_type": "fire",
    "severity": "high",
    "location": "53.3498,-6.2603",
    "has_images": true
  },
  "ip_address": "127.0.0.1"
}
```

---

## Step 3: Test Validation Errors

### 3.1 Location Outside Dublin Bounds

```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 51.5074,
    "location_lng": -0.1278,
    "disaster_type": "fire",
    "severity": "high",
    "description": "This is in London, should fail"
  }'
```

**Expected Response** (422 Unprocessable Entity):
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "location_lat"],
      "msg": "Value error, Latitude must be within Dublin bounds (53.2 to 53.5)",
      "input": 51.5074
    }
  ]
}
```

---

### 3.2 Description Too Short

```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.3498,
    "location_lng": -6.2603,
    "disaster_type": "fire",
    "severity": "high",
    "description": "Fire"
  }'
```

**Expected Response** (422 Unprocessable Entity):
```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "description"],
      "msg": "String should have at least 10 characters",
      "input": "Fire"
    }
  ]
}
```

---

### 3.3 Too Many Images

```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.3498,
    "location_lng": -6.2603,
    "disaster_type": "fire",
    "severity": "high",
    "description": "Fire with too many images",
    "image_urls": [
      "https://example.com/1.jpg",
      "https://example.com/2.jpg",
      "https://example.com/3.jpg",
      "https://example.com/4.jpg",
      "https://example.com/5.jpg",
      "https://example.com/6.jpg"
    ]
  }'
```

**Expected Response** (422 Unprocessable Entity):
```json
{
  "detail": [
    {
      "type": "too_long",
      "loc": ["body", "image_urls"],
      "msg": "List should have at most 5 items after validation",
      "input": [...]
    }
  ]
}
```

---

## Step 4: Test Authentication Errors

### 4.1 Missing Authorization Header

```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -d '{
    "location_lat": 53.3498,
    "location_lng": -6.2603,
    "disaster_type": "fire",
    "severity": "high",
    "description": "Fire without authentication"
  }'
```

**Expected Response** (401 Unauthorized):
```json
{
  "detail": "Missing authorization header"
}
```

---

### 4.2 Invalid Session Token

```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer invalid_token_12345" \
  -d '{
    "location_lat": 53.3498,
    "location_lng": -6.2603,
    "disaster_type": "fire",
    "severity": "high",
    "description": "Fire with invalid token"
  }'
```

**Expected Response** (401 Unauthorized):
```json
{
  "detail": "Invalid or expired session token"
}
```

---

## Step 5: Test All Disaster Types

### Valid Disaster Types

1. **Flood**
```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.35,
    "location_lng": -6.26,
    "disaster_type": "flood",
    "severity": "medium",
    "description": "Heavy flooding on main street after storm"
  }'
```

2. **Accident**
```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.34,
    "location_lng": -6.25,
    "disaster_type": "accident",
    "severity": "critical",
    "description": "Multi-vehicle accident on highway with injuries"
  }'
```

3. **Medical**
```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.36,
    "location_lng": -6.27,
    "disaster_type": "medical",
    "severity": "critical",
    "description": "Mass casualty incident at public event"
  }'
```

4. **Other**
```bash
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.33,
    "location_lng": -6.24,
    "disaster_type": "other",
    "severity": "low",
    "description": "Suspicious package found in public area"
  }'
```

---

## Step 6: Test ERT Member Reporting

### 6.1 Create ERT User

First, create an ERT user by manually updating the database:

```sql
-- Connect to database
psql -U username -d drs_backend

-- Update user to ERT role
UPDATE users SET role = 'ert' WHERE mobile_number = '+919876543211';

-- Verify
SELECT user_id, mobile_number, role FROM users;
```

### 6.2 Report as ERT Member

```bash
# Use ERT user's session token
curl -X POST "http://localhost:8000/api/v1/disasters/report" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ERT_USER_SESSION_TOKEN" \
  -d '{
    "location_lat": 53.345,
    "location_lng": -6.265,
    "disaster_type": "medical",
    "severity": "high",
    "description": "ERT member on-scene reporting medical emergency"
  }'
```

**Expected Response**:
```json
{
  "disaster_id": 5,
  "location_lat": 53.345,
  "location_lng": -6.265,
  "disaster_type": "medical",
  "severity": "high",
  "description": "ERT member on-scene reporting medical emergency",
  "reporter_id": 2,
  "status": "pending",
  "image_urls": [],
  "created_at": "2026-02-02T11:00:00Z",
  "ert_notified": null
}
```

**Note**: `ert_notified: null` indicates no notifications were triggered (ERT reporters don't notify other ERTs).

---

## Step 7: Verify Database Records

```sql
-- Connect to database
psql -U username -d drs_backend

-- View all disasters
SELECT disaster_id, disaster_type, severity, status, reporter_id, created_at
FROM disasters
ORDER BY created_at DESC;

-- View disaster with location
SELECT disaster_id, disaster_type, severity,
       location_lat, location_lng,
       ST_AsText(location) as location_point,
       description
FROM disasters
WHERE disaster_id = 1;

-- Count disasters by severity
SELECT severity, COUNT(*) as count
FROM disasters
GROUP BY severity
ORDER BY count DESC;

-- Get pending disasters (for ERT assignment)
SELECT disaster_id, disaster_type, severity, created_at
FROM disasters
WHERE status = 'pending'
ORDER BY
  CASE severity
    WHEN 'critical' THEN 1
    WHEN 'high' THEN 2
    WHEN 'medium' THEN 3
    WHEN 'low' THEN 4
  END,
  created_at ASC;
```

---

## Step 8: Run Automated Tests

```bash
# Install pytest if not already installed
pip install pytest

# Run all tests
pytest app/tests/ -v

# Run only disaster API tests
pytest app/tests/unit/test_disaster_api.py -v

# Run with coverage
pytest --cov=app app/tests/

# Run specific test
pytest app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_report_disaster_success -v
```

**Expected Output**:
```
app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_report_disaster_success PASSED
app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_report_disaster_unauthorized PASSED
app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_report_disaster_invalid_location PASSED
app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_report_disaster_short_description PASSED
app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_report_disaster_too_many_images PASSED
app/tests/unit/test_disaster_api.py::TestDisasterReporting::test_ert_member_no_notification PASSED
app/tests/unit/test_disaster_api.py::TestDisasterValidation::test_disaster_type_enum_validation PASSED
app/tests/unit/test_disaster_api.py::TestDisasterValidation::test_dublin_bounds_validation PASSED

========== 8 passed in 2.34s ==========
```

---

## Postman Collection

### Import This Collection

```json
{
  "info": {
    "name": "Disaster Reporting API",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "item": [
    {
      "name": "Auth - Request OTP",
      "request": {
        "method": "POST",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": {
          "mode": "raw",
          "raw": "{\"mobile_number\": \"+919876543210\"}"
        },
        "url": "http://localhost:8000/api/v1/auth/signup/request-otp"
      }
    },
    {
      "name": "Auth - Verify OTP",
      "request": {
        "method": "POST",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": {
          "mode": "raw",
          "raw": "{\"mobile_number\": \"+919876543210\", \"otp_code\": \"123456\"}"
        },
        "url": "http://localhost:8000/api/v1/auth/signup/verify"
      }
    },
    {
      "name": "Report Disaster",
      "request": {
        "method": "POST",
        "header": [
          {"key": "Content-Type", "value": "application/json"},
          {"key": "Authorization", "value": "Bearer {{session_token}}"}
        ],
        "body": {
          "mode": "raw",
          "raw": "{\"location_lat\": 53.3498, \"location_lng\": -6.2603, \"disaster_type\": \"fire\", \"severity\": \"high\", \"description\": \"Large fire at city center building\"}"
        },
        "url": "http://localhost:8000/api/v1/disasters/report"
      }
    }
  ]
}
```

---

## Troubleshooting

### Error: "Missing authorization header"
- **Cause**: No `Authorization` header in request
- **Solution**: Add `-H "Authorization: Bearer YOUR_TOKEN"` to curl command

### Error: "Latitude must be within Dublin bounds"
- **Cause**: Coordinates outside Dublin area (53.2-53.5 lat, -6.5 to -6.0 lng)
- **Solution**: Use coordinates within Dublin bounds

### Error: "type 'geography' does not exist"
- **Cause**: PostGIS extension not enabled
- **Solution**: Run `CREATE EXTENSION IF NOT EXISTS postgis;` in database

### Error: "Invalid or expired session token"
- **Cause**: Token expired (24h) or doesn't exist in Redis
- **Solution**: Request new OTP and verify to get new session token

### Error: Connection refused
- **Cause**: Application not running or wrong port
- **Solution**: Start app with `uvicorn main:app --reload`

---

## Performance Testing

### Load Test with Apache Bench

```bash
# Test 100 requests with 10 concurrent connections
ab -n 100 -c 10 \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -p disaster_payload.json \
  http://localhost:8000/api/v1/disasters/report
```

**disaster_payload.json**:
```json
{
  "location_lat": 53.3498,
  "location_lng": -6.2603,
  "disaster_type": "fire",
  "severity": "high",
  "description": "Load testing disaster report"
}
```

---

## Next Steps

1. ✅ All validation tests pass
2. ✅ Authentication works correctly
3. ✅ Audit logs are being written
4. ✅ Database records created successfully
5. ⏳ Integrate Developer 2's features (AI verification, ERT notifications)
6. ⏳ Add more endpoints (GET disasters, UPDATE status, etc.)
7. ⏳ Deploy to staging environment

---

## API Documentation

For interactive API documentation, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
