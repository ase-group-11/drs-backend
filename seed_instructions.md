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
100 PENDING reports seeded (4 per location cluster × 25 clusters)
    ↓
Celery task: process_pending_reports (every 30s)
    ↓  Lead report per cluster (created_at = now−30 min → processed FIRST)
Creates ACTIVE disaster → DirectCoordinationClient
    ├─ Auto-dispatches nearest AVAILABLE units
    ├─ HttpRerouteClient → POST /reroute/trigger  (if road_blocked or severity≥HIGH)
    └─ EvacuationService.plan+activate            (if severity=CRITICAL)
    ↓  Corroborating reports 2-4 (created_at = now−1…8 min → processed AFTER lead)
Flagged DUPLICATE (disaster exists nearby AND report age < 15 min)
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

## Bug History — Root Causes and Fixes

### Bug 1 — 200 active disasters created instead of ~25

**Symptom:** Calling the seed then waiting for Celery resulted in ~200 ACTIVE disasters, far more than the 25 expected clusters.

**Root cause:** The DUPLICATE detection rule in `app/services/evaluation/rules_engine.py` requires **both** conditions:

```python
if ctx.nearby_report_count >= 1 and ctx.report_age_minutes < 15:
    return EvaluationFlag.DUPLICATE.name
```

The previous seed set `created_at = now − random(2, 15 min)` for **all** reports including corroborating ones. Since `get_pending_reports` orders by `created_at ASC` (oldest first), any corroborating report that was given an older timestamp than the lead was processed **before** the lead. At that point no disaster existed yet → no nearby reports → the corroborating report created its own disaster.

**Fix:** Lead report: `created_at = now − 30 min` (always oldest → always processed first). Corroborating reports: `created_at = now − random(1, 8 min)` (always newer than lead AND age < 15 min when Celery evaluates → DUPLICATE rule fires).

---

### Bug 2 — Only 1-2 deployment entries despite many "units dispatched" WebSocket alerts

**Symptom:** WebSocket received multiple `disaster.dispatched` events but `GET /deployments` showed only 1-2 entries. Frontend showed 0 units assigned.

**Root cause A — Unit exhaustion:** 50 clusters × 2-6 units per disaster = 100-300 units needed, but only 52 units seeded. Later disasters in the same Celery batch found 0 AVAILABLE units for their service types. `DirectCoordinationClient.trigger_deploy` logs a warning and returns without creating any deployment records. The `disaster.dispatched` RabbitMQ event was still fired (from `dispatch_units` result) but with 0 actual deployments.

**Root cause B — 409 abort:** `DeploymentService.dispatch_units` raises HTTP 409 `ConflictException` if a selected unit is not AVAILABLE at transaction time. In `DirectCoordinationClient.trigger_deploy`, all selected unit IDs are passed to `dispatch_units` in one call. If even one unit in the list became DEPLOYED between `list_available_units()` and `dispatch_units()` (possible when many disasters are processed in rapid succession), the entire batch fails → 0 deployment records → exception swallowed silently by outer `try/except`.

**Fix:** Reduced to 25 clusters. With 52 available units and 25 disasters at mixed severities, most disasters can claim units without exhausting the pool. Worst case: 25 CRITICAL disasters × 3 units × 3 services = 225 units (still exceeds fleet, but in practice severities are mixed and not all service types apply to every disaster type).

**Monitoring:** If you still see 0 deployments for some disasters, check Celery logs:
```
DirectCoordinationClient.trigger_deploy: no available AMBULANCE units for disaster=<uuid>
```
This is expected behaviour once the pool is exhausted — not a bug.

---

### Bug 3 — Emergency unit map pins on river / sea

**Symptom:** Some unit pins on the live map appeared on the River Corrib (Galway) and River Shannon bridge midspan (Athlone).

**Root cause:** Station coordinates were placed on the river channel rather than the adjacent road.

**Fix:**
- Galway Fire Station: `(53.2743, −9.0488)` → `(53.2755, −9.0509)` (Flood Street area, road-level)
- Athlone Fire Station: `(53.4228, −7.9408)` → `(53.4235, −7.9378)` (Church Street area, off bridge)
- Same fix applied to Galway Rapid Response Unit and National Command Support (Galway).

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

### Step 2 — Create 24 ERT Team Members

24 Emergency Response Team members with realistic Irish names, covering all departments.

**No MANAGER role is used anywhere — ADMIN + STAFF only.**

| Department | Count | Roles |
|------------|-------|-------|
| FIRE | 7 | 1 ADMIN + 6 STAFF |
| MEDICAL | 7 | 1 ADMIN + 6 STAFF |
| POLICE | 7 | 1 ADMIN + 6 STAFF |
| IT | 1 | 1 ADMIN only (no STAFF for IT) |
| RESCUE COORDINATION | 2 | 1 ADMIN + 1 STAFF |

**Password for all ERT members:** `Password123!`

Key members used throughout the seed:

| Variable | Index | Name | Phone | Dept |
|----------|-------|------|-------|------|
| `fire_admin` | `[0]` | Cdr James Brennan | +353871100001 | FIRE ADMIN |
| `med_admin` | `[7]` | Dr Fiona Ryan | +353871100007 | MEDICAL ADMIN |
| `police_admin` | `[14]` | Supt Claire O'Connor | +353871100013 | POLICE ADMIN |

---

### Step 3 — Create 100 Citizen Users

100 citizens with realistic Irish names and sequential phone numbers (`+353851000001` through `+353851000100`). Each gets:
- Role: `RESIDENT`, Status: `ACTIVE`
- Email derived from phone: `353851000001@citizen.drs`

---

### Step 4 — Create 52 Emergency Units Across Ireland

Units cover every valid `UnitType` value **except HAZMAT** (removed by design):

| Type | Count | Stations |
|------|-------|---------|
| `FIRE_ENGINE` | 12 | 12 real Irish fire stations: Tara St, Phibsborough, Finglas, Dún Laoghaire, Tallaght, Cork, Galway\*, Limerick, Waterford, Sligo, Kilkenny, Athlone\* |
| `AMBULANCE` | 10 | 10 real Irish hospitals: St James's, Beaumont, Tallaght UH, CUH, UHG, UHL, UHW, Sligo UH, LUH Letterkenny, Mayo UH |
| `PATROL_CAR` | 10 | 8 Garda stations + 2 additional at Dublin stations |
| `RAPID_RESPONSE` | 6 | Dublin, Cork, Galway\*, Limerick, Waterford, Sligo city centres |
| `RESCUE` | 6 | Dublin Mountain, Galway Coastal, Kerry Mountain, Cork Water, Wicklow Mountain, Donegal Sea |
| `COMMAND` | 4 | Dublin Mobile, Cork Regional, Limerick Regional, National (Galway\*) |

\* Galway and Athlone coordinates fixed to road-level positions (off rivers) — see Bug 3.

All units start as `AVAILABLE`.

---

### Step 5 — Create 100 PENDING Disaster Reports

**This is the core of the seed.** No disasters or deployments are created directly. Instead:

- 25 location clusters × 4 PENDING reports = **100 total PENDING reports**
- All reports have `report_status = PENDING` and no `disaster_id`
- The Celery background task processes these over the next ~1-2 minutes

#### Structure Per Cluster (4 reports)

| Report | Role | `created_at` | Why |
|--------|------|--------------|-----|
| 1 (lead) | Creates the disaster | `now − 30 min` | Oldest → processed FIRST by `ORDER BY created_at ASC` |
| 2–4 (corroborate) | DUPLICATE after disaster exists | `now − 1…8 min` | Newer → processed AFTER lead; age < 15 min → DUPLICATE rule fires |

#### DUPLICATE Rule (from `rules_engine.py`)

```python
if ctx.nearby_report_count >= 1 and ctx.report_age_minutes < 15:
    return EvaluationFlag.DUPLICATE.name
```

Both conditions must hold. Corroborating reports are set to `now − 1…8 min` so they are **always less than 15 minutes old** when the Celery worker evaluates them. The lead report's disaster (created 30s–2 min earlier in the same or previous batch) provides `nearby_report_count >= 1`.

---

### Step 6 — Create 30 Active Trips

30 citizens (`citizens[0]` through `citizens[29]`) are registered as having active trips near disaster zones. Used by the reroute (UC7) and evacuation (UC8) services to demonstrate affected vehicle re-routing.

---

### Step 7 — Commit

All DB writes are committed in a single transaction.

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
  "message": "Seed complete — all tables reset. 100 PENDING reports across 25 location clusters seeded...",
  "summary": {
    "ert_members": 24,
    "citizens": 100,
    "emergency_units": 52,
    "pending_reports": 100,
    "lead_reports": 25,
    "corroborating_reports": 75,
    "location_clusters": 25,
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
    "step_1": "Celery worker processes PENDING reports every 30 s (up to 50 per batch)",
    "step_2": "Lead report per cluster (now−30 min) → processed first → new ACTIVE disaster",
    "step_3": "Units auto-dispatched by DirectCoordinationClient (nearest AVAILABLE first)",
    "step_4": "Corroborating reports 2-4 (now−1…8 min) → age < 15 min → DUPLICATE",
    "step_5": "road_blocked=True or severity>=HIGH → reroute triggered (HttpRerouteClient)",
    "step_6": "CRITICAL severity → evacuation plan created + activated automatically",
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

Second batch (~60s after seed) processes remaining 50 reports (the corroborating ones):
```
process_pending_reports: found 50 pending report(s)
process_pending_reports: report <uuid> → flag=DUPLICATE (disaster <uuid> nearby)
...
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
GET /api/v1/disasters/               → should show ~25 active disasters
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

### 2. Wait ~30-60s for first Celery batch (lead reports → disasters created)

### 3. Wait another ~30s for second Celery batch (corroborating reports → DUPLICATE)

### 4. Use Case 4 — Live Map
```
GET /api/v1/live-map/disasters    → all active disasters with map pins
GET /api/v1/live-map/units        → all deployed units with GPS coordinates
```

### 5. Use Case 6 — Deployments
```
GET /api/v1/disasters/{id}/deployments     → dispatched units per disaster
POST /api/v1/deployments/{id}/update-status
  { "status": "on_scene" }
POST /api/v1/deployments/{id}/update-status
  { "status": "completed" }
```
Unit returns to `AVAILABLE` status automatically.

### 6. Use Case 7 — Reroute (already triggered automatically)
```
GET /api/v1/reroute/plans          → active reroute plans
```

### 7. Use Case 8 — Evacuation (already triggered for CRITICAL)
```
GET /api/v1/evacuations/           → active evacuation plans
POST /api/v1/evacuations/{id}/approve
POST /api/v1/evacuations/{id}/activate
```

### 8. Resolve a Disaster
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
| `EmergencyTeamRole` | `ADMIN`, `STAFF` (no MANAGER) |
| `UserRole` | `RESIDENT` |
| `UserStatus` | `ACTIVE` |

---

## Raw SQL Enum Patterns

All enum values in raw SQL inserts use `.name` (Python attribute name, uppercase) rather than `.value` (which may be lowercase for some enums):

```python
# ✓ Correct — DB enum values are uppercase
UserStatus.ACTIVE.name    → "ACTIVE"   (not .value = "active")
UserRole.RESIDENT.name    → "RESIDENT" (not .value = "user")
UnitType.AMBULANCE.name   → "AMBULANCE" (not .value = "ambulance")
UnitStatus.AVAILABLE.name → "AVAILABLE" (not .value = "available")

# EmergencyTeamRole, Department, DisasterType, DisasterSeverity are fine with .value
EmergencyTeamRole.ADMIN.value → "ADMIN" ✓
Department.FIRE.value         → "FIRE"  ✓
DisasterType.FIRE.value       → "FIRE"  ✓
DisasterSeverity.HIGH.value   → "HIGH"  ✓
```

### PostGIS SQL Patterns

```sql
-- Point geography
ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography

-- Custom enum cast (required for PostgreSQL parameterised queries)
CAST(:type AS disaster_type)
CAST(:severity AS disaster_severity)
CAST('PENDING' AS disaster_report_status)   -- uppercase!
```

### Why raw SQL (not ORM `db.add()`)?

SQLAlchemy 2.x uses `insertmanyvalues` for batch inserts, which requires the returned PK to match the Python UUID string used as a sentinel. This fails when the DB returns a native UUID type and Python compares it as a string. Using raw `text()` inserts bypasses this mechanism entirely.

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
