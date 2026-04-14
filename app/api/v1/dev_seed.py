# app/api/v1/dev_seed.py
"""
DEV-ONLY: Focused single-disaster seed for end-to-end demo and testing.

POST /api/v1/dev/seed  ─  single call wipes everything and seeds one disaster scenario.

═══════════════════════════════════════════════════════════════════════
DESIGN: Pipeline-first — only PENDING reports are seeded.
═══════════════════════════════════════════════════════════════════════

Default: active_disasters=1, reports_per_disaster=3

  1. Wipes ALL tables (FK-safe order, SAVEPOINT per table)
  2. Creates 10 ERT members (FIRE 3 / MEDICAL 3 / POLICE 3 / IT 1)
  3. Creates 20 citizen users
  4. Creates 9 Dublin emergency units (FIRE_ENGINE×2, AMBULANCE×2,
     PATROL_CAR×2, RAPID_RESPONSE×1, RESCUE×1, COMMAND×1), capacity=2
  5. Creates 3 PENDING disaster reports for O'Connell St CRITICAL FIRE
       — Lead report:        created_at = now − 30 min  (processed first)
       — Corroborating 2–3:  created_at = now − 1…8 min (processed after → DUPLICATE)
  6. Creates 8 active trips with positions guaranteed within 1 km of the
     disaster so the reroute pipeline always finds affected vehicles.

After seed the Celery evaluation task (every 60 s) takes over:
  • Lead report  → new ACTIVE disaster (CRITICAL FIRE)
  • DirectCoordinationClient → dispatches nearest AVAILABLE units
  • DirectRerouteClient → triggers reroute (road_blocked=True + CRITICAL)
  • Evacuation → triggered automatically (CRITICAL severity)
  • Corroborating reports → DUPLICATE (nearby disaster + age < 15 min)

Register in main.py (already done):
    if settings.ENVIRONMENT != "production":
        from app.api.v1 import dev_seed
        app.include_router(dev_seed.router, prefix="/api/v1")
"""

import uuid
import logging
import random
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.session import get_db
from app.db.models.enums import (
    EmergencyTeamRole,
    Department,
    UnitType,
    UnitStatus,
    UserStatus,
    UserRole,
    DisasterType,
    DisasterSeverity,
)
from app.auth.password_handler import hash_password
from app.auth.jwt_handler import create_access_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Dev Seed"])

TEAM_PASSWORD = "Password123!"


# ─────────────────────────────────────────────────────────────────────────────
# Request body model
# ─────────────────────────────────────────────────────────────────────────────

class SeedRequest(BaseModel):
    active_disasters: int = Field(
        default=1,
        ge=1,
        le=10,
        description=(
            "How many disaster clusters to seed. Scenarios are taken from the top "
            "of DISASTER_SCENARIOS in order, so index 0 (O'Connell St CRITICAL fire) "
            "is always included first."
        ),
    )
    reports_per_disaster: int = Field(
        default=3,
        ge=1,
        le=4,
        description=(
            "Total reports per disaster cluster including the lead report. "
            "1 = lead only. 3 = lead + 2 corroborating (default). "
            "Corroborating reports are created at now−1…8 min → always DUPLICATE "
            "when Celery evaluates them (age < 15 min + nearby disaster exists)."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dublin emergency unit stations
# ─────────────────────────────────────────────────────────────────────────────

FIRE_STATIONS = [
    {"name": "Tara Street Fire Station",   "lat": 53.3451, "lon": -6.2592, "city": "Dublin"},
    {"name": "Phibsborough Fire Station",  "lat": 53.3630, "lon": -6.2749, "city": "Dublin"},
]

AMBULANCE_STATIONS = [
    {"name": "St James's Hospital, Dublin", "lat": 53.3414, "lon": -6.2928, "city": "Dublin"},
    {"name": "Beaumont Hospital, Dublin",   "lat": 53.3906, "lon": -6.2386, "city": "Dublin"},
]

GARDA_STATIONS = [
    {"name": "Pearse Street Garda Station", "lat": 53.3444, "lon": -6.2482, "city": "Dublin"},
    {"name": "Store Street Garda Station",  "lat": 53.3497, "lon": -6.2471, "city": "Dublin"},
]

# ─────────────────────────────────────────────────────────────────────────────
# Disaster scenarios — index 0 is always the primary demo scenario
# ─────────────────────────────────────────────────────────────────────────────

DISASTER_SCENARIOS = [
    # Position 0: CRITICAL FIRE — triggers deploy + reroute + evacuation
    {
        "lat": 53.3441, "lon": -6.2675,
        "address": "O'Connell Street, Dublin 1",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Catastrophic fire in a multi-storey commercial building. Entire building involved, "
            "roof collapse imminent. Mass evacuation of O'Connell Street corridor underway. "
            "Multiple casualties confirmed."
        ),
        "people_affected": 450, "multiple_casualties": True,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Position 1: HIGH FLOOD — triggers deploy + reroute (road_blocked)
    {
        "lat": 53.3498, "lon": -6.2295,
        "address": "Grand Canal Dock, Dublin 2",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": (
            "Severe flooding in Docklands area following storm surge. "
            "Water levels rising rapidly, ground floors submerged, roads impassable."
        ),
        "people_affected": 320, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Position 2: MEDIUM STORM — triggers deploy only
    {
        "lat": 53.3350, "lon": -6.2620,
        "address": "St Stephen's Green, Dublin 2",
        "type": DisasterType.STORM, "severity": DisasterSeverity.MEDIUM,
        "description": (
            "Severe windstorm causing widespread tree falls and structural damage. "
            "Multiple roads blocked by fallen trees."
        ),
        "people_affected": 80, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# 20 Irish citizen names + phone numbers
# ─────────────────────────────────────────────────────────────────────────────

CITIZEN_DATA = [
    ("Aoife Murphy",      "+353851000001"), ("Ciarán Kelly",       "+353851000002"),
    ("Siobhán Walsh",     "+353851000003"), ("Seán O'Brien",       "+353851000004"),
    ("Niamh Byrne",       "+353851000005"), ("Conor Ryan",         "+353851000006"),
    ("Aisling O'Connor",  "+353851000007"), ("Darragh Doyle",      "+353851000008"),
    ("Roisín O'Neill",    "+353851000009"), ("Pádraig McCarthy",   "+353851000010"),
    ("Orla Sullivan",     "+353851000011"), ("Eoin Gallagher",     "+353851000012"),
    ("Caoimhe Doherty",   "+353851000013"), ("Declan Quinn",       "+353851000014"),
    ("Fiona Fitzgerald",  "+353851000015"), ("Brendan Hughes",     "+353851000016"),
    ("Sorcha O'Dwyer",    "+353851000017"), ("Killian Brennan",    "+353851000018"),
    ("Mairead Kennedy",   "+353851000019"), ("Cathal Farrell",     "+353851000020"),
]

# ─────────────────────────────────────────────────────────────────────────────
# 10 ERT members — ADMIN + STAFF only (no MANAGER)
# Index layout:
#   FIRE    → 0–2  (1 ADMIN + 2 STAFF)
#   MEDICAL → 3–5  (1 ADMIN + 2 STAFF)
#   POLICE  → 6–8  (1 ADMIN + 2 STAFF)
#   IT      → 9    (1 ADMIN only)
# ─────────────────────────────────────────────────────────────────────────────

ERT_TEAM_DATA = [
    # FIRE (index 0–2)
    ("Cdr James Brennan",    "+353871100001", "jbrennan@drs.ie",  EmergencyTeamRole.ADMIN, Department.FIRE),
    ("FF Patrick O'Brien",   "+353871100003", "pobrien@drs.ie",   EmergencyTeamRole.STAFF, Department.FIRE),
    ("FF Sinéad Walsh",      "+353871100004", "swalsh@drs.ie",    EmergencyTeamRole.STAFF, Department.FIRE),
    # MEDICAL (index 3–5)
    ("Dr Fiona Ryan",        "+353871100007", "fryan@drs.ie",     EmergencyTeamRole.ADMIN, Department.MEDICAL),
    ("Paramedic Niamh Lee",  "+353871100009", "nlee@drs.ie",      EmergencyTeamRole.STAFF, Department.MEDICAL),
    ("EMT Laura Byrne",      "+353871100011", "lbyrne@drs.ie",    EmergencyTeamRole.STAFF, Department.MEDICAL),
    # POLICE (index 6–8)
    ("Supt Claire O'Connor", "+353871100013", "coconnor@drs.ie",  EmergencyTeamRole.ADMIN, Department.POLICE),
    ("Sgt Orla Doherty",     "+353871100015", "odoherty@drs.ie",  EmergencyTeamRole.STAFF, Department.POLICE),
    ("Garda Tomás Nolan",    "+353871100016", "tnolan@drs.ie",    EmergencyTeamRole.STAFF, Department.POLICE),
    # IT (index 9)
    ("IT Dir Ciara Higgins", "+353871100019", "chiggins@drs.ie",  EmergencyTeamRole.ADMIN, Department.IT),
]


def _uid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/dev/seed", summary="[DEV] Full DB wipe and focused single-disaster seed")
async def seed_full_database(
    body: SeedRequest = Body(default_factory=SeedRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Wipes ALL tables and seeds a minimal focused dataset for end-to-end testing.

    Default: 1 disaster cluster, 3 reports (1 lead + 2 corroborating).

    **active_disasters** — how many disaster clusters (1–10).
    **reports_per_disaster** — total reports per cluster (1–4).

    Everything downstream (disaster creation, unit dispatch, reroute,
    evacuation) is handled automatically by the Celery evaluation pipeline.
    Returns JWT tokens for immediate Postman use.
    """
    now = datetime.utcnow()
    n_clusters    = body.active_disasters
    n_per_cluster = body.reports_per_disaster

    # ── STEP 1: Wipe everything (FK-safe, SAVEPOINT per table) ───────────────
    wipe_tables = [
        "disaster_chat_sessions",
        "audit_logs",
        "traffic_overrides",
        "reroute_plans",
        "evacuation_plans",
        "disaster_photos",
        "deployments",
        "disaster_reports",
        "disasters",
        "active_trips",
        "unit_crew",
        "emergency_units",
        "emergency_teams",
        "users",
        "road_segments",
    ]
    for tbl in wipe_tables:
        try:
            async with db.begin_nested():
                await db.execute(text(f"DELETE FROM {tbl}"))
            logger.debug(f"[dev/seed] cleared {tbl}")
        except Exception as exc:
            logger.warning(f"[dev/seed] could not clear {tbl}: {exc}")

    await db.flush()

    # ── STEP 2: Create ERT members ────────────────────────────────────────────
    pw_hash = hash_password(TEAM_PASSWORD)

    class _Row:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    ert_members: List[_Row] = []

    for full_name, phone, email, role, dept in ERT_TEAM_DATA:
        mid = _uid()
        await db.execute(text("""
            INSERT INTO emergency_teams (
                id, phone_number, password_hash, full_name, email,
                role, department, status,
                created_at, updated_at
            ) VALUES (
                :id, :phone, :pw, :name, :email,
                CAST(:role AS emergency_team_role),
                CAST(:dept AS department),
                CAST(:status AS user_status),
                :now, :now
            )
        """), {
            "id": mid, "phone": phone, "pw": pw_hash, "name": full_name,
            "email": email, "role": role.value, "dept": dept.value,
            "status": UserStatus.ACTIVE.name, "now": now,
        })
        ert_members.append(_Row(id=mid, phone_number=phone, full_name=full_name))

    # Convenience refs — indices match ERT_TEAM_DATA layout above
    fire_admin   = ert_members[0]  # Cdr James Brennan     — FIRE ADMIN
    med_admin    = ert_members[3]  # Dr Fiona Ryan         — MEDICAL ADMIN
    police_admin = ert_members[6]  # Supt Claire O'Connor  — POLICE ADMIN

    logger.info(f"[dev/seed] created {len(ert_members)} ERT members")

    # ── STEP 3: Create 20 citizen users ──────────────────────────────────────
    citizens: List[_Row] = []

    for full_name, phone in CITIZEN_DATA:
        cid = _uid()
        email = f"{phone.replace('+', '').replace(' ', '')}@citizen.drs"
        await db.execute(text("""
            INSERT INTO users (
                id, phone_number, full_name, email,
                role, status,
                created_at, updated_at
            ) VALUES (
                :id, :phone, :name, :email,
                CAST(:role AS user_role),
                CAST(:status AS user_status),
                :now, :now
            )
        """), {
            "id": cid, "phone": phone, "name": full_name, "email": email,
            "role": UserRole.RESIDENT.name, "status": UserStatus.ACTIVE.name,
            "now": now,
        })
        citizens.append(_Row(id=cid, phone_number=phone, full_name=full_name))

    logger.info(f"[dev/seed] created {len(citizens)} citizens")

    # ── STEP 4: Create 9 Dublin emergency units ───────────────────────────────
    unit_specs = []

    for i, stn in enumerate(FIRE_STATIONS):
        unit_specs.append({
            "code": f"UNIT-FIRE-{i+1:03d}",
            "name": f"Fire Engine {i+1} — {stn['city']}",
            "type": UnitType.FIRE_ENGINE,
            "dept": Department.FIRE,
            "station": stn,
        })

    for i, stn in enumerate(AMBULANCE_STATIONS):
        unit_specs.append({
            "code": f"UNIT-AMB-{i+1:03d}",
            "name": f"Ambulance {i+1} — {stn['city']}",
            "type": UnitType.AMBULANCE,
            "dept": Department.MEDICAL,
            "station": stn,
        })

    for i, stn in enumerate(GARDA_STATIONS):
        unit_specs.append({
            "code": f"UNIT-POL-{i+1:03d}",
            "name": f"Patrol Car {i+1} — {stn['city']}",
            "type": UnitType.PATROL_CAR,
            "dept": Department.POLICE,
            "station": stn,
        })

    unit_specs.append({
        "code": "UNIT-RR-001",
        "name": "Rapid Response 1 — Dublin",
        "type": UnitType.RAPID_RESPONSE,
        "dept": Department.FIRE,
        "station": {"name": "Dublin City Rapid Response", "lat": 53.3518, "lon": -6.2605, "city": "Dublin"},
    })

    unit_specs.append({
        "code": "UNIT-RES-001",
        "name": "Rescue Unit 1 — Dublin",
        "type": UnitType.RESCUE,
        "dept": Department.FIRE,
        "station": {"name": "Dublin Mountain Rescue", "lat": 53.2441, "lon": -6.3877, "city": "Dublin"},
    })

    unit_specs.append({
        "code": "UNIT-CMD-001",
        "name": "Command Vehicle 1 — Dublin",
        "type": UnitType.COMMAND,
        "dept": Department.FIRE,
        "station": {"name": "Dublin Mobile Command", "lat": 53.3430, "lon": -6.2557, "city": "Dublin"},
    })

    emergency_units: List[_Row] = []
    for spec in unit_specs:
        stn = spec["station"]
        uid = _uid()
        await db.execute(text("""
            INSERT INTO emergency_units (
                id, unit_code, unit_name, unit_type, department,
                station_name, station_location,
                unit_status, capacity, total_deployments,
                created_at, updated_at
            ) VALUES (
                :id, :code, :name,
                CAST(:utype AS unit_type),
                CAST(:dept AS department),
                :station_name,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                CAST(:status AS unit_status),
                :capacity, :total_deployments,
                :now, :now
            )
        """), {
            "id": uid, "code": spec["code"], "name": spec["name"],
            "utype": spec["type"].name, "dept": spec["dept"].value,
            "station_name": stn["name"],
            "lon": stn["lon"], "lat": stn["lat"],
            "status": UnitStatus.AVAILABLE.name,
            "capacity": 2, "total_deployments": 0,
            "now": now,
        })
        emergency_units.append(_Row(id=uid, unit_type=spec["type"], unit_code=spec["code"]))

    logger.info(f"[dev/seed] created {len(emergency_units)} emergency units")

    # ── STEP 4b: Assign 1 crew member per unit ───────────────────────────────
    _FIRE_INDICES   = [i for i, (_, _, _, _, dept) in enumerate(ERT_TEAM_DATA) if dept == Department.FIRE]
    _MED_INDICES    = [i for i, (_, _, _, _, dept) in enumerate(ERT_TEAM_DATA) if dept == Department.MEDICAL]
    _POLICE_INDICES = [i for i, (_, _, _, _, dept) in enumerate(ERT_TEAM_DATA) if dept == Department.POLICE]

    _UNIT_DEPT_MAP = {
        UnitType.FIRE_ENGINE:    _FIRE_INDICES,
        UnitType.RAPID_RESPONSE: _FIRE_INDICES,
        UnitType.RESCUE:         _FIRE_INDICES,
        UnitType.COMMAND:        _FIRE_INDICES,
        UnitType.AMBULANCE:      _MED_INDICES,
        UnitType.PATROL_CAR:     _POLICE_INDICES,
    }

    crew_assignments = 0
    _dept_counters = {"fire": 0, "med": 0, "police": 0}

    for eu in emergency_units:
        member_indices = _UNIT_DEPT_MAP.get(eu.unit_type)
        if not member_indices:
            continue

        if eu.unit_type in (UnitType.FIRE_ENGINE, UnitType.RAPID_RESPONSE, UnitType.RESCUE, UnitType.COMMAND):
            ckey = "fire"
        elif eu.unit_type == UnitType.AMBULANCE:
            ckey = "med"
        else:
            ckey = "police"

        # 1 crew member per unit
        idx = member_indices[_dept_counters[ckey] % len(member_indices)]
        member = ert_members[idx]
        _dept_counters[ckey] += 1
        await db.execute(text("""
            INSERT INTO unit_crew (unit_id, team_member_id)
            VALUES (:uid, :mid)
            ON CONFLICT DO NOTHING
        """), {"uid": eu.id, "mid": member.id})
        crew_assignments += 1

    await db.flush()
    logger.info(f"[dev/seed] created {crew_assignments} unit_crew assignments")

    # ── STEP 5: Create PENDING disaster reports ───────────────────────────────
    #
    # Lead report:        created_at = now − 30 min → processed FIRST by Celery
    #                     → creates ACTIVE disaster → triggers reroute/evacuation/deploy
    # Corroborating 2–N:  created_at = now − 1…8 min → processed AFTER lead
    #                     → age < 15 min + nearby disaster exists → DUPLICATE

    CORROBORATE_DESCRIPTIONS = [
        "I can confirm this incident near {addr}. Situation is serious and worsening.",
        "Witnessed the emergency at {addr}. Urgent response needed.",
        "Multiple people are affected at {addr}. Please send help immediately.",
    ]

    def jitter():
        """Random coordinate offset ±300 m (~0.003°) — within 2 km dedup radius."""
        return (random.random() - 0.5) * 0.006

    selected_scenarios = DISASTER_SCENARIOS[:n_clusters]
    citizen_idx = 0
    report_count = 0

    for sc in selected_scenarios:
        # Lead report
        lead_citizen = citizens[citizen_idx % len(citizens)]
        citizen_idx += 1

        await db.execute(text("""
            INSERT INTO disaster_reports (
                id, user_id, disaster_type, severity, description,
                location, location_address,
                people_affected, multiple_casualties, structural_damage, road_blocked,
                report_status, created_at, updated_at
            ) VALUES (
                :id, :user_id, CAST(:dtype AS disaster_type),
                CAST(:severity AS disaster_severity), :description,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                :address, :people_affected,
                :multiple_casualties, :structural_damage, :road_blocked,
                CAST('PENDING' AS disaster_report_status),
                :created_at, :updated_at
            )
        """), {
            "id":                  _uid(),
            "user_id":             lead_citizen.id,
            "dtype":               sc["type"].value,
            "severity":            sc["severity"].value,
            "description":         sc["description"],
            "lon":                 sc["lon"],
            "lat":                 sc["lat"],
            "address":             sc["address"],
            "people_affected":     sc["people_affected"],
            "multiple_casualties": sc["multiple_casualties"],
            "structural_damage":   sc["structural_damage"],
            "road_blocked":        sc["road_blocked"],
            "created_at":          now - timedelta(minutes=30),
            "updated_at":          now,
        })
        report_count += 1

        # Corroborating reports
        for j in range(n_per_cluster - 1):
            c = citizens[citizen_idx % len(citizens)]
            citizen_idx += 1
            desc = CORROBORATE_DESCRIPTIONS[j % len(CORROBORATE_DESCRIPTIONS)].format(addr=sc["address"])
            corr_severity = (
                sc["severity"].value
                if j == 0
                else (
                    DisasterSeverity.HIGH.value
                    if sc["severity"] in (DisasterSeverity.CRITICAL, DisasterSeverity.HIGH)
                    else DisasterSeverity.MEDIUM.value
                )
            )
            await db.execute(text("""
                INSERT INTO disaster_reports (
                    id, user_id, disaster_type, severity, description,
                    location, location_address,
                    people_affected, multiple_casualties, structural_damage, road_blocked,
                    report_status, created_at, updated_at
                ) VALUES (
                    :id, :user_id, CAST(:dtype AS disaster_type),
                    CAST(:severity AS disaster_severity), :description,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                    :address, :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    CAST('PENDING' AS disaster_report_status),
                    :created_at, :updated_at
                )
            """), {
                "id":                  _uid(),
                "user_id":             c.id,
                "dtype":               sc["type"].value,
                "severity":            corr_severity,
                "description":         desc,
                "lon":                 sc["lon"] + jitter(),
                "lat":                 sc["lat"] + jitter(),
                "address":             sc["address"],
                "people_affected":     random.randint(
                    max(1, sc["people_affected"] // 8),
                    max(2, sc["people_affected"] // 4),
                ),
                "multiple_casualties": sc["multiple_casualties"],
                "structural_damage":   sc["structural_damage"],
                "road_blocked":        sc["road_blocked"],
                "created_at":          now - timedelta(minutes=random.randint(1, 8)),
                "updated_at":          now,
            })
            report_count += 1

    await db.flush()
    logger.info(
        "[dev/seed] created %d PENDING reports across %d cluster(s)",
        report_count, n_clusters,
    )

    # ── STEP 6: Create active trips routed THROUGH each disaster zone ────────
    #
    # Each trip has its current position on one side of the disaster and its
    # destination on the opposite side, so the straight-line path between them
    # passes directly through the incident point.  This makes the reroute
    # visually meaningful — without it the route would go through the blocked
    # area; with it TomTom returns a detour around it.
    #
    # For Dublin lat ~53.3°: 1 km ≈ 0.009° lat / 0.015° lon.
    # Current positions are ~0.8 km from the disaster centre so
    # get_users_in_affected_area() (2 km radius) always finds them.
    # Destinations are ~1.5 km on the OPPOSITE side so the path crosses the zone.
    #
    # Layout — (cur_dlat, cur_dlon,  dest_dlat, dest_dlon):
    #   N→S, S→N, E→W, W→E, NE→SW, SW→NE, NW→SE, SE→NW
    TRIP_OFFSETS = [
        ( 0.008,  0.000,  -0.013,  0.000),   # North  → South
        (-0.008,  0.000,   0.013,  0.000),   # South  → North
        ( 0.000,  0.012,   0.000, -0.020),   # East   → West
        ( 0.000, -0.012,   0.000,  0.020),   # West   → East
        ( 0.006,  0.009,  -0.010, -0.015),   # NE     → SW
        (-0.006, -0.009,   0.010,  0.015),   # SW     → NE
        ( 0.006, -0.009,  -0.010,  0.015),   # NW     → SE
        (-0.006,  0.009,   0.010, -0.015),   # SE     → NW
    ]

    trip_count = 0
    for i, sc in enumerate(selected_scenarios):
        for j, (cdlat, cdlon, ddlat, ddlon) in enumerate(TRIP_OFFSETS):
            c = citizens[(citizen_idx + i * len(TRIP_OFFSETS) + j) % len(citizens)]
            cur_lat = sc["lat"] + cdlat
            cur_lng = sc["lon"] + cdlon
            # Destination on the opposite side — path crosses the disaster zone
            dest_lat = sc["lat"] + ddlat
            dest_lng = sc["lon"] + ddlon
            await db.execute(text("""
                INSERT INTO active_trips (
                    id, user_id,
                    current_lat, current_lng,
                    dest_lat, dest_lng,
                    vehicle_type,
                    expires_at, created_at, updated_at
                ) VALUES (
                    :id, :user_id,
                    :current_lat, :current_lng,
                    :dest_lat, :dest_lng,
                    :vehicle_type,
                    :expires_at, :created_at, :updated_at
                )
                ON CONFLICT (user_id) DO UPDATE SET
                    current_lat  = EXCLUDED.current_lat,
                    current_lng  = EXCLUDED.current_lng,
                    dest_lat     = EXCLUDED.dest_lat,
                    dest_lng     = EXCLUDED.dest_lng,
                    vehicle_type = EXCLUDED.vehicle_type,
                    expires_at   = EXCLUDED.expires_at,
                    updated_at   = EXCLUDED.updated_at
            """), {
                "id":           _uid(),
                "user_id":      c.id,
                "current_lat":  cur_lat,
                "current_lng":  cur_lng,
                "dest_lat":     dest_lat,
                "dest_lng":     dest_lng,
                "vehicle_type": random.choice(["general", "general", "public_transport"]),
                "expires_at":   now + timedelta(hours=4),
                "created_at":   now,
                "updated_at":   now,
            })
            trip_count += 1

    await db.flush()
    logger.info(f"[dev/seed] created {trip_count} active trips")

    # ── STEP 7: Commit ────────────────────────────────────────────────────────
    await db.commit()
    logger.info("[dev/seed] committed all seed data")

    # ── STEP 8: Generate tokens ───────────────────────────────────────────────
    fire_admin_token   = create_access_token(fire_admin.id,   "emergency_team")
    med_admin_token    = create_access_token(med_admin.id,    "emergency_team")
    police_admin_token = create_access_token(police_admin.id, "emergency_team")
    citizen_token      = create_access_token(citizens[0].id,  "user")

    scenario_summary = [
        {
            "index":    i,
            "address":  sc["address"],
            "type":     sc["type"].value,
            "severity": sc["severity"].value,
        }
        for i, sc in enumerate(selected_scenarios)
    ]

    return {
        "message": (
            f"Seed complete — {report_count} PENDING reports across {n_clusters} cluster(s) "
            f"({n_clusters} lead at now−30 min + {report_count - n_clusters} corroborating at now−1…8 min). "
            "Celery evaluation task (every 60 s) will create the disaster, dispatch units, "
            "trigger reroute and evacuation automatically."
        ),
        "seed_config": {
            "active_disasters_requested": n_clusters,
            "reports_per_disaster":       n_per_cluster,
            "total_pending_reports":      report_count,
            "lead_reports":               n_clusters,
            "corroborating_reports":      report_count - n_clusters,
            "active_trips_seeded":        trip_count,
        },
        "scenarios_seeded": scenario_summary,
        "summary": {
            "ert_members":           len(ert_members),
            "citizens":              len(citizens),
            "emergency_units":       len(emergency_units),
            "unit_crew_assignments": crew_assignments,
            "pending_reports":       report_count,
            "active_trips":          trip_count,
            "active_disasters":      0,
            "deployments":           0,
        },
        "tokens": {
            "fire_admin_token":   fire_admin_token,
            "med_admin_token":    med_admin_token,
            "police_admin_token": police_admin_token,
            "citizen_token":      citizen_token,
        },
        "login_credentials": {
            "password":           TEAM_PASSWORD,
            "fire_admin_phone":   fire_admin.phone_number,
            "med_admin_phone":    med_admin.phone_number,
            "police_admin_phone": police_admin.phone_number,
            "citizen_phone":      citizens[0].phone_number,
        },
        "what_happens_next": {
            "step_1": "Celery processes PENDING reports every 60 s (auto_evaluate_pending_reports)",
            "step_2": "Lead report (now−30 min) → processed first → new ACTIVE disaster",
            "step_3": "DirectCoordinationClient dispatches nearest AVAILABLE units",
            "step_4": "DirectRerouteClient triggers reroute (road_blocked=True + severity)",
            "step_5": "CRITICAL severity → evacuation plan created and activated",
            "step_6": "Corroborating reports (now−1…8 min) → age < 15 min → DUPLICATE",
            "monitor": "kubectl logs -n drs -l app=drs-celery-worker -f",
        },
        "note": (
            "Use fire_admin_token as Bearer for ERT/admin endpoints. "
            "citizen_token for citizen endpoints. "
            "Re-call POST /dev/seed at any time for a fresh clean slate."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Reset endpoint — wipe only, no re-seed
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/dev/seed/reset", summary="[DEV] Wipe all tables without re-seeding")
async def reset_database(db: AsyncSession = Depends(get_db)):
    """
    Wipes all tables in FK-safe order. Does NOT create any test data.
    Use POST /dev/seed for a full wipe + seed cycle.
    """
    wipe_tables = [
        "disaster_chat_sessions",
        "audit_logs",
        "traffic_overrides",
        "reroute_plans",
        "evacuation_plans",
        "disaster_photos",
        "deployments",
        "disaster_reports",
        "disasters",
        "active_trips",
        "unit_crew",
        "emergency_units",
        "emergency_teams",
        "users",
        "road_segments",
    ]
    cleared = []
    skipped = []

    for tbl in wipe_tables:
        try:
            async with db.begin_nested():
                await db.execute(text(f"DELETE FROM {tbl}"))
            cleared.append(tbl)
        except Exception as exc:
            logger.warning(f"[dev/seed/reset] could not clear {tbl}: {exc}")
            skipped.append(tbl)

    await db.commit()
    return {"cleared": cleared, "skipped": skipped}
