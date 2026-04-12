# app/api/v1/dev_seed.py
"""
DEV-ONLY: Seed endpoint for end-to-end testing.

POST /api/v1/dev/seed
  - Clears all disaster / deployment / reroute / evacuation data
  - Resets deployed units back to AVAILABLE
  - Creates 3 ERT teams + 6 emergency units + 1 citizen (skip if already exist)
  - Returns JWT tokens for immediate use — no OTP flow required

emergency_teams / emergency_units / users rows that already exist are
NOT deleted; records are created only when the phone / unit_code is absent.
Run repeatedly without side-effects.
"""

import uuid
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.session import get_db
from app.db.models.emergency_team import EmergencyTeam
from app.db.models.emergency_unit import EmergencyUnit
from app.db.models.user import User
from app.db.models.enums import (
    EmergencyTeamRole,
    Department,
    UnitType,
    UnitStatus,
    UserStatus,
)
from app.auth.password_handler import hash_password
from app.auth.jwt_handler import create_access_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Dev Seed"])

# ── Test identity constants ───────────────────────────────────────────────────

TEST_PHONES = {
    "fire_admin":  "+353871000001",
    "med_manager": "+353871000002",
    "pol_staff":   "+353871000003",
    "citizen":     "+353876000001",
}

TEAM_PASSWORD = "Password123!"  # dev-only, never used in production

UNIT_SEED = [
    {
        "unit_code": "UNIT-FIRE-001",
        "unit_name": "Alpha Fire Engine",
        "unit_type": UnitType.FIRE_ENGINE,
        "department": Department.FIRE,
        "station_name": "Tara St Fire Station",
        "station_lat": 53.3391,
        "station_lon": -6.2607,
    },
    {
        "unit_code": "UNIT-FIRE-002",
        "unit_name": "Bravo Fire Engine",
        "unit_type": UnitType.FIRE_ENGINE,
        "department": Department.FIRE,
        "station_name": "Finglas Fire Station",
        "station_lat": 53.3764,
        "station_lon": -6.2989,
    },
    {
        "unit_code": "UNIT-MED-001",
        "unit_name": "Alpha Ambulance",
        "unit_type": UnitType.AMBULANCE,
        "department": Department.MEDICAL,
        "station_name": "St James's Hospital",
        "station_lat": 53.3498,
        "station_lon": -6.2603,
    },
    {
        "unit_code": "UNIT-MED-002",
        "unit_name": "Bravo Ambulance",
        "unit_type": UnitType.AMBULANCE,
        "department": Department.MEDICAL,
        "station_name": "Beaumont Hospital",
        "station_lat": 53.3854,
        "station_lon": -6.1583,
    },
    {
        "unit_code": "UNIT-POL-001",
        "unit_name": "Alpha Patrol Car",
        "unit_type": UnitType.PATROL_CAR,
        "department": Department.POLICE,
        "station_name": "Pearse St Garda Station",
        "station_lat": 53.3322,
        "station_lon": -6.2490,
    },
    {
        "unit_code": "UNIT-HAZ-001",
        "unit_name": "HazMat Unit",
        "unit_type": UnitType.HAZMAT,
        "department": Department.FIRE,
        "station_name": "Tara St Fire Station",
        "station_lat": 53.3391,
        "station_lon": -6.2607,
    },
]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/dev/seed", summary="[DEV] Reset disaster data and seed test infrastructure")
async def seed_test_data(db: AsyncSession = Depends(get_db)):
    """
    Reset all disaster/deployment data and ensure test teams/units/citizen exist.

    Safe to call repeatedly — existing emergency_teams, emergency_units, and
    users are never deleted. New rows are inserted only when absent by
    phone_number / unit_code.

    Returns JWT tokens for the Fire ADMIN team member and the test citizen,
    ready to paste into Swagger/Postman Authorization headers.
    """

    # ── Step 1: Wipe disaster lifecycle data (FK-safe order) ─────────────────
    _RESET_TABLES = [
        "audit_logs",
        "traffic_overrides",
        "reroute_plans",
        "evacuation_plans",
        "disaster_photos",
        "deployments",
        "disaster_reports",
        "disasters",
        "active_trips",
    ]
    for tbl in _RESET_TABLES:
        try:
            await db.execute(text(f"DELETE FROM {tbl}"))
            logger.debug(f"[dev/seed] cleared {tbl}")
        except Exception as exc:
            logger.warning(f"[dev/seed] could not clear {tbl}: {exc}")

    # Reset road segments (best-effort — schema may vary)
    try:
        await db.execute(
            text("UPDATE road_segments SET disaster_id = NULL, status = 'open', reason = NULL")
        )
    except Exception:
        pass

    # Return deployed/on_scene units to AVAILABLE
    await db.execute(text(
        """
        UPDATE emergency_units
        SET unit_status = CAST('available' AS unit_status),
            last_deployed_at = NULL
        WHERE unit_status IN (
            CAST('deployed'  AS unit_status),
            CAST('on_scene'  AS unit_status),
            CAST('returning' AS unit_status)
        )
        """
    ))

    # ── Step 2: Ensure ERT teams exist ────────────────────────────────────────
    pw_hash = hash_password(TEAM_PASSWORD)

    async def get_or_create_team(
        phone: str,
        full_name: str,
        email: str,
        role: EmergencyTeamRole,
        dept: Department,
    ) -> tuple[str, bool]:
        """Return (id, created). Inserts a new team only if phone absent."""
        row = (await db.execute(
            text("SELECT id FROM emergency_teams WHERE phone_number = :p"),
            {"p": phone},
        )).first()
        if row:
            return str(row[0]), False
        team = EmergencyTeam(
            id=str(uuid.uuid4()),
            phone_number=phone,
            password_hash=pw_hash,
            full_name=full_name,
            email=email,
            role=role,
            department=dept,
            status=UserStatus.ACTIVE,
        )
        db.add(team)
        return team.id, True

    fire_admin_id, fire_created = await get_or_create_team(
        TEST_PHONES["fire_admin"],
        "Fire Commander Dublin",
        "fire.admin@test.drs",
        EmergencyTeamRole.ADMIN,
        Department.FIRE,
    )
    med_id, med_created = await get_or_create_team(
        TEST_PHONES["med_manager"],
        "Medical Lead Dublin",
        "med.lead@test.drs",
        EmergencyTeamRole.MANAGER,
        Department.MEDICAL,
    )
    pol_id, pol_created = await get_or_create_team(
        TEST_PHONES["pol_staff"],
        "Garda Liaison Dublin",
        "garda@test.drs",
        EmergencyTeamRole.STAFF,
        Department.POLICE,
    )

    # ── Step 3: Ensure emergency units exist ──────────────────────────────────
    from geoalchemy2.elements import WKTElement  # local import to avoid top-level dep issues

    unit_results = []
    for u in UNIT_SEED:
        row = (await db.execute(
            text("SELECT id FROM emergency_units WHERE unit_code = :c"),
            {"c": u["unit_code"]},
        )).first()
        if row:
            unit_results.append({
                "unit_code": u["unit_code"],
                "unit_name": u["unit_name"],
                "id": str(row[0]),
                "created": False,
            })
            continue

        # WKT POINT is (longitude latitude)
        loc = WKTElement(
            f"POINT({u['station_lon']} {u['station_lat']})",
            srid=4326,
        )
        unit = EmergencyUnit(
            id=str(uuid.uuid4()),
            unit_code=u["unit_code"],
            unit_name=u["unit_name"],
            unit_type=u["unit_type"],
            department=u["department"],
            station_name=u["station_name"],
            station_location=loc,
            unit_status=UnitStatus.AVAILABLE,
        )
        db.add(unit)
        unit_results.append({
            "unit_code": u["unit_code"],
            "unit_name": u["unit_name"],
            "id": unit.id,
            "created": True,
        })

    # ── Step 4: Ensure citizen exists ─────────────────────────────────────────
    cit_row = (await db.execute(
        text("SELECT id FROM users WHERE phone_number = :p"),
        {"p": TEST_PHONES["citizen"]},
    )).first()
    if cit_row:
        citizen_id = str(cit_row[0])
        citizen_created = False
    else:
        citizen = User(
            id=str(uuid.uuid4()),
            phone_number=TEST_PHONES["citizen"],
            full_name="Test Citizen Dublin",
            email="citizen@test.drs",
            status=UserStatus.ACTIVE,
        )
        db.add(citizen)
        citizen_id = citizen.id
        citizen_created = True

    # ── Step 5: Commit everything ─────────────────────────────────────────────
    await db.commit()
    logger.info("[dev/seed] seed complete")

    # ── Step 6: Return tokens + summary ──────────────────────────────────────
    admin_token   = create_access_token(fire_admin_id, "emergency_team")
    citizen_token = create_access_token(citizen_id, "user")

    return {
        "message": "Seed complete — disaster data reset, test infrastructure ensured",
        "teams": [
            {
                "id": fire_admin_id,
                "full_name": "Fire Commander Dublin",
                "phone": TEST_PHONES["fire_admin"],
                "role": "ADMIN",
                "department": "FIRE",
                "created": fire_created,
            },
            {
                "id": med_id,
                "full_name": "Medical Lead Dublin",
                "phone": TEST_PHONES["med_manager"],
                "role": "MANAGER",
                "department": "MEDICAL",
                "created": med_created,
            },
            {
                "id": pol_id,
                "full_name": "Garda Liaison Dublin",
                "phone": TEST_PHONES["pol_staff"],
                "role": "STAFF",
                "department": "POLICE",
                "created": pol_created,
            },
        ],
        "units": unit_results,
        "citizen": {
            "id": citizen_id,
            "full_name": "Test Citizen Dublin",
            "phone": TEST_PHONES["citizen"],
            "created": citizen_created,
        },
        "tokens": {
            "ert_admin_token": admin_token,
            "citizen_token": citizen_token,
        },
        "login_password": TEAM_PASSWORD,
        "note": (
            "Copy ert_admin_token / citizen_token into Swagger Authorize → Bearer <token>. "
            "Re-run at any time to get a clean slate."
        ),
    }
