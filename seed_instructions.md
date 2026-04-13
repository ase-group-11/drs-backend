# DRS Dev Seed — Full Reference Guide

**Endpoint:** `POST /api/v1/dev/seed`
**File:** `app/api/v1/dev_seed.py`
**Registered:** automatically when `ENVIRONMENT != "production"` (see `app/main.py`)

---

## Purpose

A single API call that wipes the entire database and replaces it with realistic, demo-ready data spanning all of Ireland. The seed creates **only PENDING disaster reports** — no pre-created disasters, no pre-created deployments. This means the **entire automatic evaluation pipeline** runs for real:

```
POST /dev/seed
    ↓
400 PENDING reports seeded (8 per location cluster × 50 clusters)
    ↓
Celery task: process_pending_reports (every 30s)
    ↓  First report per cluster
Creates ACTIVE disaster → DirectCoordinationClient
    ├─ Auto-dispatches nearest AVAILABLE units
    ├─ HttpRerouteClient → POST /reroute/trigger  (if road_blocked or severity≥HIGH)
    └─ EvacuationService.plan+activate            (if severity=CRITICAL)
    ↓  Reports 2-8 per cluster
Flagged DUPLICATE (disaster already exists nearby)
```

---

## Quick Start

```bash
# Wipe + seed everything
curl -X POST http://<host>/api/v1/dev/seed

# Or in Swagger: POST /api/v1/dev/seed → Execute
```

The response includes four JWT tokens ready for immediate use in Swagger's `Authorize` dialog or Postman.

**Watch the pipeline run:**
```bash
kubectl logs -n drs -l app=drs-celery-worker -f
```

---

## What the Endpoint Does — Step by Step

### Step 1 — Wipe All Tables (FK-safe order)

The following tables are deleted in an order that respects all foreign key constraints:

| Order | Table |
|-------|-------|
| 1 | `audit_logs` |
| 2 | `traffic_overrides` |
| 3 | `reroute_plans` |
| 4 | `evacuation_plans` |
| 5 | `disaster_photos` |
| 6 | `deployments` |
| 7 | `disaster_reports` |
| 8 | `disasters` |
| 9 | `active_trips` |
| 10 | `unit_crew` |
| 11 | `emergency_units` |
| 12 | `emergency_teams` |
| 13 | `users` |
| 14 | `road_segments` |

Each delete is wrapped in try/except — if a table doesn't exist or fails, a warning is logged and the seed continues.

---

### Step 2 — Create 30 ERT Team Members

30 Emergency Response Team members with realistic Irish names, covering all departments.

| Department | Count | Roles |
|------------|-------|-------|
| FIRE | 8 | 1 ADMIN, 1 MANAGER, 6 STAFF |
| MEDICAL | 8 | 1 ADMIN, 1 MANAGER, 6 STAFF |
| POLICE | 8 | 1 ADMIN, 1 MANAGER, 6 STAFF |
| IT | 4 | 1 ADMIN, 3 STAFF |
| RESCUE COORDINATION | 2 | 1 ADMIN, 1 STAFF |

**Password for all ERT members:** `Password123!`

Key members used throughout the seed:
- `ert_members[0]` — Cdr James Brennan, **FIRE ADMIN** (token: `fire_admin_token`)
- `ert_members[8]` — Dr Fiona Ryan, **MEDICAL ADMIN** (token: `med_admin_token`)
- `ert_members[16]` — Supt Claire O'Connor, **POLICE ADMIN** (token: `police_admin_token`)

---

### Step 3 — Create 100 Citizen Users

100 citizens with realistic Irish names and sequential phone numbers (`+353851000001` through `+353851000100`). Each gets:
- Role: `RESIDENT`, Status: `ACTIVE`
- Email derived from phone: `353851000001@citizen.drs`

Citizens are used as report submitters and active trip registrants.

---

### Step 4 — Create 62 Emergency Units Across Ireland

Units cover every valid `UnitType` value **except HAZMAT** (removed by design):

| Type | Count | Stations |
|------|-------|---------|
| `FIRE_ENGINE` | 12 | 12 real Irish fire stations: Tara St, Phibsborough, Finglas, Dún Laoghaire, Tallaght, Cork, Galway, Limerick, Waterford, Sligo, Kilkenny, Athlone |
| `AMBULANCE` | 14 | 10 real Irish hospitals + 4 additional depots: St James's, Beaumont, Tallaght UH, CUH, UHG, UHL, UHW, Sligo UH, LUH Letterkenny, Mayo UH |
| `PATROL_CAR` | 10 | Pearse St, Store St, Rathmines, Blanchardstown, Cork, Galway, Limerick, Waterford Garda stations |
| `RAPID_RESPONSE` | 6 | Dublin, Cork, Galway, Limerick, Waterford, Sligo city centres |
| `RESCUE` | 6 | Dublin Mountain, Galway Coastal, Kerry Mountain, Cork Water, Wicklow Mountain, Donegal Sea |
| `COMMAND` | 4 | Dublin Mobile, Cork Regional, Limerick Regional, National (Galway) |

All units start as `AVAILABLE`. The evaluation pipeline's `DirectCoordinationClient` automatically dispatches the nearest available units by unit type when a disaster is created.

---

### Step 5 — Create 400 PENDING Disaster Reports

**This is the core of the seed.** No disasters or deployments are created directly. Instead:

- 50 location clusters × 8 PENDING reports = **400 total PENDING reports**
- All reports have `report_status = PENDING` and no `disaster_id`
- The Celery background task processes these over the next ~2-3 minutes

#### Structure Per Cluster (8 reports)

| Report | Role | Severity | Coordinates |
|--------|------|----------|-------------|
| 1 (lead) | Creates the disaster | Full scenario severity | Exact scenario coordinates |
| 2–4 (corroborate) | DUPLICATE after disaster exists | Same severity | ±300m jitter |
| 5–8 (corroborate) | DUPLICATE after disaster exists | HIGH or MEDIUM | ±300m jitter |

The **lead report** has:
- Exact scenario coordinates
- Full severity (CRITICAL/HIGH/MEDIUM as per scenario)
- `road_blocked=True` where relevant (triggers reroute)
- Realistic detailed description (triggers high evaluation confidence)

The **7 corroborating reports** have:
- Slight coordinate jitter (±300m) — simulate nearby-but-distinct reporters
- Varying descriptions confirming the same incident
- Same `road_blocked` flag — ensures reroute is triggered consistently

#### Why This Design Works

The `process_pending_reports` Celery task processes up to 50 reports per batch in sequence. When the **lead report** is processed:
1. Evaluation service finds no existing active disaster nearby
2. Rules engine + XGBoost classifies it as VERIFIED (high confidence for HIGH/CRITICAL severity with casualties + structural damage)
3. A new `ACTIVE` disaster is created
4. `DirectCoordinationClient.trigger_deploy()` dispatches nearest available units by type
5. `HttpRerouteClient.trigger_reroute()` is called if `road_blocked=True` or severity ≥ HIGH
6. `DirectCoordinationClient.trigger_evacuation()` is called if severity = CRITICAL

When **corroborating reports 2-8** are processed (next batch or same batch, after lead):
1. Evaluation service finds the existing active disaster nearby
2. DUPLICATE flag is set
3. Original disaster's confidence is boosted
4. No new deployments or reroutes triggered

---

### Step 6 — Create 30 Active Trips

30 citizens (`citizens[0]` through `citizens[29]`) are registered as having active trips near disaster zones. Used by the reroute (UC7) and evacuation (UC8) services to demonstrate affected vehicle re-routing.

Each trip:
- `current_lat/lng` — near a disaster scenario location (±2.5km)
- `dest_lat/lng` — even closer to disaster (±1km)
- `vehicle_type` — `general` or `public_transport`
- `expires_at` — 4 hours from seed time
- Uses `ON CONFLICT (user_id) DO UPDATE` — safe to re-seed

---

### Step 7 — Commit

All DB writes are committed in a single transaction. If any step throws an unhandled exception, the entire seed rolls back (FastAPI's `get_db` dependency handles this).

---

### Step 8 — Return Tokens

```json
{
  "tokens": {
    "fire_admin_token":   "<jwt>",
    "med_admin_token":    "<jwt>",
    "police_admin_token": "<jwt>",
    "citizen_token":      "<jwt>"
  }
}
```

---

## Response Body

```json
{
  "message": "Seed complete — all tables reset. 400 PENDING reports across 50 location clusters seeded. The Celery evaluation task (runs every 30s) will evaluate each report...",
  "summary": {
    "ert_members": 30,
    "citizens": 100,
    "emergency_units": 62,
    "pending_reports": 400,
    "location_clusters": 50,
    "active_trips": 30,
    "active_disasters": 0,
    "deployments": 0
  },
  "tokens": { ... },
  "login_credentials": {
    "password": "Password123!",
    "fire_admin_phone": "+353871100001",
    "med_admin_phone": "+353871100007",
    "police_admin_phone": "+353871100013",
    "citizen_phone": "+353851000001"
  },
  "what_happens_next": {
    "step_1": "Celery worker processes PENDING reports every 30s (up to 50 per batch)",
    "step_2": "First report per cluster → disaster created → units dispatched automatically",
    "step_3": "Remaining 7 reports per cluster → flagged DUPLICATE (disaster already exists)",
    "step_4": "road_blocked=True or severity>=HIGH → reroute triggered automatically",
    "step_5": "CRITICAL severity disasters → evacuation plan created + activated automatically",
    "monitor": "kubectl logs -n drs -l app=drs-celery-worker -f"
  }
}
```

---

## Watching the Pipeline (Post-Seed)

### 1. Celery worker logs
```bash
kubectl logs -n drs -l app=drs-celery-worker -f
```
Expected output (first run, ~30s after seed):
```
process_pending_reports: found 50 pending report(s)
process_pending_reports: report <uuid> → severity=HIGH confidence=0.87 flag=NORMAL
...
process_pending_reports: batch done — 50 evaluated, 0 failed
```

### 2. Consumer logs (downstream events)
```bash
kubectl logs -n drs -l app=drs-consumer -f
```
Expected:
```
[notification_consumer] disaster.evaluated → delivering to N users
[notification_consumer] disaster.dispatched → N units dispatched
```

### 3. Verify results via API (use `fire_admin_token`)
```
GET /api/v1/disasters/               → should show ~50 active disasters
GET /api/v1/emergency-units/         → many units show status=deployed
GET /api/v1/disaster-reports/all     → mix of VERIFIED + DUPLICATE + PENDING
GET /api/v1/evacuations/             → evacuation plans for CRITICAL disasters
```

---

## End-to-End Demo Flow (Using Seed Data)

### 0. Seed
```
POST /api/v1/dev/seed
```
Save all tokens from response.

### 1. Connect WebSocket (before waiting)
```
ws://<host>/api/v1/ws/notifications?token=<citizen_token>
```
You will receive `disaster.evaluated`, `disaster.dispatched`, and `evacuation.triggered` events as Celery processes the reports.

### 2. Wait ~30-60s for Celery to run

### 3. Use Case 4 — Live Map
```
GET /api/v1/live-map/disasters    → all active disasters with map pins
GET /api/v1/live-map/units        → all deployed units with GPS coordinates
```

### 4. Use Case 6 — Deployments
```
GET /api/v1/disasters/{id}/deployments     → dispatched units per disaster
POST /api/v1/deployments/{id}/update-status
  { "status": "on_scene" }
POST /api/v1/deployments/{id}/update-status
  { "status": "completed" }
```
Unit returns to `AVAILABLE` status automatically.

### 5. Use Case 7 — Reroute (already triggered automatically)
```
GET /api/v1/reroute/plans          → active reroute plans
```
Or trigger manually:
```
POST /api/v1/reroute/trigger
{ "disaster_id": "<uuid>" }
```

### 6. Use Case 8 — Evacuation (already triggered for CRITICAL)
```
GET /api/v1/evacuations/           → active evacuation plans
POST /api/v1/evacuations/{id}/approve
POST /api/v1/evacuations/{id}/activate
```

### 7. Resolve a Disaster
```
POST /api/v1/disasters/{id}/resolve
{ "resolution_notes": "Incident cleared." }
```

---

## Enum Reference

| Enum | Values Used |
|------|-------------|
| `DisasterType` | `FIRE`, `FLOOD`, `STORM` |
| `DisasterSeverity` | `MEDIUM`, `HIGH`, `CRITICAL` (all scenarios) |
| `UnitType` | `FIRE_ENGINE`, `AMBULANCE`, `PATROL_CAR`, `RAPID_RESPONSE`, `RESCUE`, `COMMAND` (no HAZMAT) |
| `UnitStatus` | `AVAILABLE` (all units at seed time) |
| `Department` | `FIRE`, `MEDICAL`, `POLICE`, `IT` |
| `EmergencyTeamRole` | `ADMIN`, `MANAGER`, `STAFF` |
| `UserRole` | `RESIDENT` |
| `UserStatus` | `ACTIVE` |

---

## PostGIS SQL Patterns Used

```sql
-- Point geography
ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography

-- Custom enum cast (required for PostgreSQL parameterised queries)
CAST(:type AS disaster_type)
CAST(:severity AS disaster_severity)
CAST('pending' AS disaster_report_status)
```

---

## Production Safety

```python
# app/main.py — never registered in production
if settings.ENVIRONMENT != "production":
    from app.api.v1 import dev_seed
    app.include_router(dev_seed.router, prefix="/api/v1")
```

---

## Re-seeding

Call `POST /api/v1/dev/seed` again at any time to reset everything and start fresh. Always use tokens from the most recent seed call (UUIDs change each time).
