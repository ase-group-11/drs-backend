# app/api/v1/dev_seed.py
"""
DEV-ONLY: Modular seed endpoints for end-to-end demo and testing.

Each endpoint populates exactly one concern so you can build up the dataset
incrementally — useful for testing individual features without re-seeding
everything.

Endpoints
─────────
POST /dev/seed/reset          Wipe ALL tables (FK-safe order)
POST /dev/reset               Alias for /dev/seed/reset

POST /dev/seed/teams          Seed N ERT members  {count, dept_split}
POST /dev/seed/users          Seed N citizen users  {count}
POST /dev/seed/units          Seed N emergency units  {count, max_crew}
POST /dev/seed/disasters      Seed N disasters + reports  {count, with_reroutes,
                              with_evacuations, reports_per_cluster}
POST /dev/seed/trips          Seed N active trips crossing live disaster zones
                              {count}  (reads active disasters from DB)
POST /dev/seed/all            Run all of the above in one call

Coordinate strategy
───────────────────
• 20 disaster scenarios spread across the Republic of Ireland:
    Dublin (8) • Cork (3) • Galway (2) • Limerick (2) • Waterford (1)
    Kerry (1)  • Sligo (1) • Donegal (1) • Wicklow (1)
• Evacuation disasters are Dublin-only (shelters hardcoded in evacuation_service)
• Reroute disasters are spread across Ireland (road_blocked=True, HIGH/CRITICAL)
• Trips are ≥ 20 km end-to-end and always cross the disaster zone so reroutes
  are visually clear on the map
"""

import uuid
import logging
import random
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

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

DEFAULT_PASSWORD = "Password123!"


# ═══════════════════════════════════════════════════════════════════════════════
# Static data pools
# ═══════════════════════════════════════════════════════════════════════════════

# ── Irish first / last name pools ────────────────────────────────────────────
_FIRST = [
    "Aoife", "Ciarán", "Siobhán", "Seán", "Niamh", "Conor", "Aisling",
    "Darragh", "Roisín", "Pádraig", "Orla", "Eoin", "Caoimhe", "Declan",
    "Fiona", "Brendan", "Sorcha", "Killian", "Mairéad", "Cathal", "Deirdre",
    "Fergus", "Grainne", "Liam", "Maeve", "Noel", "Oonagh", "Peadar",
    "Róisín", "Tadhg", "Úna", "Vivienne", "Winifred", "Cormac", "Dónal",
    "Eimear", "Fiach", "Gearóid", "Hanna", "Imelda", "James", "Karen",
    "Lorcan", "Múirne", "Naoise", "Odhrán", "Prionsias", "Quinlan",
]

_LAST = [
    "Murphy", "Kelly", "Walsh", "O'Brien", "Byrne", "Ryan", "O'Connor",
    "Doyle", "O'Neill", "McCarthy", "Sullivan", "Gallagher", "Doherty",
    "Quinn", "Fitzgerald", "Hughes", "O'Dwyer", "Brennan", "Kennedy",
    "Farrell", "Burke", "Collins", "O'Sullivan", "Connolly", "Clarke",
    "Nolan", "Barry", "Higgins", "Kavanagh", "Lynch", "Moore", "O'Reilly",
    "Brady", "Dunne", "Fleming", "Griffin", "Healy", "Jordan", "Kenny",
]

_ERT_TITLES = {
    Department.FIRE:    ["Cdr", "Lt", "Capt", "FF", "FF"],
    Department.MEDICAL: ["Dr", "Paramedic", "EMT", "Nurse", "EMT"],
    Department.POLICE:  ["Supt", "Sgt", "Garda", "Garda", "Garda"],
    Department.IT:      ["IT Dir", "Sr Eng", "Sys Admin", "Dev", "Dev"],
}

def _make_ert_name(dept: Department, idx: int) -> str:
    title = _ERT_TITLES[dept][idx % len(_ERT_TITLES[dept])]
    first = _FIRST[(idx * 7 + dept.value.__hash__() % 13) % len(_FIRST)]
    last  = _LAST[(idx * 11 + dept.value.__hash__() % 17) % len(_LAST)]
    return f"{title} {first} {last}"


# ── Emergency unit stations spread across Ireland ────────────────────────────
#   (lat, lon, city, UnitType, Department)
UNIT_STATION_POOL: List[Dict[str, Any]] = [
    # ─── Dublin ───────────────────────────────────────────────────────────────
    {"name": "Tara Street Fire Station",       "lat": 53.3451, "lon": -6.2592, "city": "Dublin",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Phibsborough Fire Station",       "lat": 53.3630, "lon": -6.2749, "city": "Dublin",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Rathfarnham Fire Station",        "lat": 53.3064, "lon": -6.2991, "city": "Dublin",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Donnybrook Fire Station",         "lat": 53.3188, "lon": -6.2295, "city": "Dublin",
     "utype": UnitType.RAPID_RESPONSE, "dept": Department.FIRE},
    {"name": "St James's Hospital",            "lat": 53.3414, "lon": -6.2928, "city": "Dublin",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    {"name": "Beaumont Hospital",              "lat": 53.3906, "lon": -6.2386, "city": "Dublin",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    {"name": "Tallaght Hospital",              "lat": 53.2867, "lon": -6.3745, "city": "Dublin",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    {"name": "Pearse Street Garda Station",    "lat": 53.3444, "lon": -6.2482, "city": "Dublin",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    {"name": "Store Street Garda Station",     "lat": 53.3497, "lon": -6.2471, "city": "Dublin",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    {"name": "Blanchardstown Garda Station",   "lat": 53.3900, "lon": -6.3800, "city": "Dublin",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    {"name": "Dublin Mountain Rescue",         "lat": 53.2441, "lon": -6.3877, "city": "Dublin",
     "utype": UnitType.RESCUE,        "dept": Department.FIRE},
    {"name": "Dublin Mobile Command",          "lat": 53.3430, "lon": -6.2557, "city": "Dublin",
     "utype": UnitType.COMMAND,       "dept": Department.FIRE},
    {"name": "Dublin Hazmat Unit",             "lat": 53.3350, "lon": -6.2620, "city": "Dublin",
     "utype": UnitType.HAZMAT,        "dept": Department.FIRE},
    # ─── Cork ─────────────────────────────────────────────────────────────────
    {"name": "Cork City Fire Station",         "lat": 51.8985, "lon": -8.4756, "city": "Cork",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Anglesea Street Garda Station",  "lat": 51.8960, "lon": -8.4710, "city": "Cork",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    {"name": "Cork University Hospital",       "lat": 51.8910, "lon": -8.4950, "city": "Cork",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    # ─── Galway ───────────────────────────────────────────────────────────────
    {"name": "Galway City Fire Brigade",       "lat": 53.2720, "lon": -9.0540, "city": "Galway",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Mill Street Garda Station",      "lat": 53.2707, "lon": -9.0568, "city": "Galway",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    {"name": "University Hospital Galway",     "lat": 53.2740, "lon": -9.0600, "city": "Galway",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    # ─── Limerick ─────────────────────────────────────────────────────────────
    {"name": "Limerick Fire & Rescue",         "lat": 52.6638, "lon": -8.6267, "city": "Limerick",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Henry Street Garda Station",     "lat": 52.6600, "lon": -8.6300, "city": "Limerick",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    {"name": "University Hospital Limerick",   "lat": 52.6740, "lon": -8.6400, "city": "Limerick",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    # ─── Waterford ────────────────────────────────────────────────────────────
    {"name": "Waterford Fire Station",         "lat": 52.2593, "lon": -7.1101, "city": "Waterford",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Waterford Garda Station",        "lat": 52.2580, "lon": -7.1150, "city": "Waterford",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    # ─── Kerry ────────────────────────────────────────────────────────────────
    {"name": "Killarney Fire Station",         "lat": 52.0598, "lon": -9.5044, "city": "Killarney",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Kerry General Hospital",         "lat": 52.2716, "lon": -9.6982, "city": "Tralee",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
    # ─── Sligo ────────────────────────────────────────────────────────────────
    {"name": "Sligo Fire Station",             "lat": 54.2766, "lon": -8.4761, "city": "Sligo",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Sligo Garda Station",            "lat": 54.2760, "lon": -8.4750, "city": "Sligo",
     "utype": UnitType.PATROL_CAR,    "dept": Department.POLICE},
    # ─── Donegal ──────────────────────────────────────────────────────────────
    {"name": "Letterkenny Fire Station",       "lat": 54.9551, "lon": -7.7340, "city": "Letterkenny",
     "utype": UnitType.FIRE_ENGINE,   "dept": Department.FIRE},
    {"name": "Letterkenny University Hospital","lat": 54.9500, "lon": -7.7300, "city": "Letterkenny",
     "utype": UnitType.AMBULANCE,     "dept": Department.MEDICAL},
]


# ── 20 Ireland-wide disaster scenarios ───────────────────────────────────────
#
# Layout:
#   Index  0–4   Dublin CRITICAL → evacuation triggers (Dublin shelters)
#   Index  5–9   Ireland-wide HIGH/CRITICAL road_blocked → reroute triggers
#   Index 10–19  Ireland-wide monitoring → seeded as ACTIVE directly (no pipeline)
#
# All coordinates verified against OpenStreetMap / Google Maps.
DISASTER_SCENARIOS: List[Dict[str, Any]] = [
    # ── 0–4: Dublin evacuations (CRITICAL, road_blocked) ─────────────────────
    {
        "lat": 53.3498, "lon": -6.2603,
        "address": "O'Connell Street, Dublin 1",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Catastrophic fire engulfing a 10-storey commercial building on O'Connell St. "
            "Roof collapse imminent, entire block evacuated, multiple casualties confirmed."
        ),
        "people_affected": 480, "multiple_casualties": True,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3471, "lon": -6.2789,
        "address": "Smithfield Square, Dublin 7",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Major gas explosion and subsequent fire at Smithfield Square market complex. "
            "Buildings structurally compromised, widespread fire spread, mass casualty event."
        ),
        "people_affected": 350, "multiple_casualties": True,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.2867, "lon": -6.3745,
        "address": "Tallaght Town Centre, Dublin 24",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Critical fire in Tallaght Shopping Centre with entrapment reported. "
            "Multiple floors ablaze, N81 and Belgard Road closed, evacuation ordered."
        ),
        "people_affected": 420, "multiple_casualties": True,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3900, "lon": -6.3800,
        "address": "Blanchardstown Centre, Dublin 15",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Large-scale fire at Blanchardstown retail complex. Multiple units involved, "
            "N3 motorway approach roads closed, emergency services managing mass evacuation."
        ),
        "people_affected": 510, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3258, "lon": -6.2304,
        "address": "Ballsbridge, Dublin 4",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Catastrophic flash flooding in Ballsbridge following Dodder river burst. "
            "Embassies Row impassable, Merrion Road closed, residents trapped in upper floors."
        ),
        "people_affected": 380, "multiple_casualties": True,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # ── 5–9: Ireland-wide reroutes (HIGH/CRITICAL, road_blocked) ─────────────
    {
        "lat": 53.3390, "lon": -6.2417,
        "address": "Grand Canal Dock, Dublin 2",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": (
            "Severe flooding in Docklands following tidal surge and heavy rainfall. "
            "Silicon Docks roads impassable, Grand Canal Street closed."
        ),
        "people_affected": 290, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 51.8985, "lon": -8.4756,
        "address": "Patrick Street, Cork City",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": (
            "Serious fire in historic Patrick Street retail district, Cork. "
            "Multiple heritage buildings threatened, Patrick Street fully closed."
        ),
        "people_affected": 250, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.2707, "lon": -9.0568,
        "address": "Eyre Square, Galway City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": (
            "Major flooding in Galway City Centre following Corrib river flooding. "
            "Eyre Square and Shop Street submerged, N17 and N18 approach roads blocked."
        ),
        "people_affected": 310, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.6638, "lon": -8.6267,
        "address": "O'Connell Street, Limerick City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": (
            "Shannon river flooding inundating Limerick city centre. "
            "King's Island fully flooded, N18 and N24 arterials blocked."
        ),
        "people_affected": 340, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.4597, "lon": -6.2181,
        "address": "Swords Town Centre, North Dublin",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": (
            "Severe windstorm causing major structural damage in Swords. "
            "Ward River flooding roads, N1 / M1 approach roads closed northbound."
        ),
        "people_affected": 190, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # ── 10–19: ACTIVE directly (monitoring, no pipeline needed) ──────────────
    {
        "lat": 53.2004, "lon": -6.0988,
        "address": "Bray Town Centre, Wicklow",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": (
            "Severe storm causing coastal flooding and structural damage in Bray. "
            "Promenade road impassable, Bray seafront evacuated."
        ),
        "people_affected": 140, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 52.2593, "lon": -7.1101,
        "address": "The Quay, Waterford City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": (
            "Suir river flooding affecting Waterford Quayside. "
            "Quay area partially impassable, commercial properties at risk."
        ),
        "people_affected": 100, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 52.0598, "lon": -9.5044,
        "address": "Killarney Town, Kerry",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": (
            "Flash flooding through Killarney National Park environs and town centre. "
            "N71 Ring of Kerry road submerged in multiple sections."
        ),
        "people_affected": 175, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 54.2766, "lon": -8.4761,
        "address": "O'Connell Street, Sligo",
        "type": DisasterType.STORM, "severity": DisasterSeverity.CRITICAL,
        "description": (
            "Exceptionally powerful Atlantic storm battering Sligo. "
            "Widespread structural damage, Garavogue river flooding city centre."
        ),
        "people_affected": 265, "multiple_casualties": True,
        "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 54.9551, "lon": -7.7340,
        "address": "Main Street, Letterkenny, Donegal",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": (
            "Swilly river overflowing into Letterkenny town centre. "
            "Main Street and Port Road impassable, retail units flooded."
        ),
        "people_affected": 130, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 54.0100, "lon": -6.4048,
        "address": "Park Street, Dundalk, Louth",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.MEDIUM,
        "description": (
            "Industrial warehouse fire in Dundalk port area. "
            "Controlled perimeter established, Dock Road closed."
        ),
        "people_affected": 55, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 53.4239, "lon": -7.9407,
        "address": "Church Street, Athlone, Westmeath",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": (
            "Shannon flooding in Athlone. Eastbank residential areas inundated, "
            "N61 closed south of town."
        ),
        "people_affected": 210, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 52.3369, "lon": -6.4633,
        "address": "Main Street, Wexford",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": (
            "Powerful south-east storm causing damage to Wexford harbour area. "
            "Paul Quay closed, fishing vessels dragged from moorings."
        ),
        "people_affected": 80, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 53.7179, "lon": -6.3561,
        "address": "West Street, Drogheda, Louth",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": (
            "Large fire at Drogheda brewery complex on the Boyne. "
            "West Street closed, adjacent buildings at risk from heat."
        ),
        "people_affected": 115, "multiple_casualties": False,
        "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 51.9200, "lon": -8.4800,
        "address": "Blackpool, Cork City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": (
            "Localised flooding in Blackpool retail park, Cork. "
            "Car park and ground floors affected, Cork North Ring Road passable."
        ),
        "people_affected": 60, "multiple_casualties": False,
        "structural_damage": False, "road_blocked": False,
        "department": Department.FIRE,
    },
]


# ── Trip crossing offsets (long routes, ~20-28 km total) ─────────────────────
#
# Layout: (cur_dlat, cur_dlon, dest_dlat, dest_dlon)
#
# Current position is ~1.5 km from disaster centre (within 2 km radius ✓).
# Destination is ~17-22 km on the OPPOSITE side (long, visually clear route).
#
# At Dublin latitude (53.3°):
#   0.014° lat ≈ 1.56 km   |   0.175° lat ≈ 19.4 km
#   0.020° lon ≈ 1.34 km   |   0.260° lon ≈ 17.4 km
LONG_TRIP_OFFSETS = [
    (+0.014,  0.000, -0.175,  0.000),   # North current → South dest (~21 km)
    (-0.014,  0.000, +0.175,  0.000),   # South current → North dest (~21 km)
    ( 0.000, +0.020,  0.000, -0.260),   # East  current → West  dest (~19 km)
    ( 0.000, -0.020,  0.000, +0.260),   # West  current → East  dest (~19 km)
    (+0.010, +0.014, -0.124, -0.184),   # NE current → SW dest (~20 km diagonal)
    (-0.010, +0.014, +0.124, -0.184),   # SE current → NW dest (~20 km diagonal)
]

VEHICLE_TYPES = ["general", "general", "general", "public_transport"]

WIPE_TABLES = [
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


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic request models
# ═══════════════════════════════════════════════════════════════════════════════

class TeamsRequest(BaseModel):
    count: int = Field(default=12, ge=4, le=100,
                       description="Total ERT members (min 4 to cover all departments).")

class UsersRequest(BaseModel):
    count: int = Field(default=40, ge=5, le=500,
                       description="Number of citizen users.")

class UnitsRequest(BaseModel):
    count: int    = Field(default=15, ge=1, le=100,
                          description="Number of emergency units to create.")
    max_crew: int = Field(default=2, ge=1, le=50,
                          description="Max crew per unit (all units get this capacity).")

class DisastersRequest(BaseModel):
    count:               int = Field(default=20, ge=1,  le=20,
                                     description="Total active disaster scenarios (max 20).")
    with_reroutes:       int = Field(default=5,  ge=0,  le=10,
                                     description="How many disasters trigger reroute pipeline.")
    with_evacuations:    int = Field(default=4,  ge=0,  le=5,
                                     description="How many disasters trigger evacuation pipeline (Dublin only, max 5).")
    reports_per_cluster: int = Field(default=3,  ge=1,  le=5,
                                     description="Reports per pipeline cluster (1 lead + N-1 corroborating).")

class TripsRequest(BaseModel):
    count: int = Field(default=24, ge=4, le=200,
                       description="Total active trips to create, spread across disaster locations.")

class AllRequest(BaseModel):
    teams:     TeamsRequest     = Field(default_factory=TeamsRequest)
    users:     UsersRequest     = Field(default_factory=UsersRequest)
    units:     UnitsRequest     = Field(default_factory=UnitsRequest)
    disasters: DisastersRequest = Field(default_factory=DisastersRequest)
    trips:     TripsRequest     = Field(default_factory=TripsRequest)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _uid() -> str:
    return str(uuid.uuid4())


async def _wipe(db: AsyncSession) -> Dict[str, List[str]]:
    """Delete all rows from every table in FK-safe order."""
    cleared, skipped = [], []
    for tbl in WIPE_TABLES:
        try:
            async with db.begin_nested():
                await db.execute(text(f"DELETE FROM {tbl}"))
            cleared.append(tbl)
        except Exception as exc:
            logger.warning(f"[dev/seed] could not clear {tbl}: {exc}")
            skipped.append(tbl)
    await db.flush()
    return {"cleared": cleared, "skipped": skipped}


async def _seed_teams(db: AsyncSession, count: int, now: datetime):
    """
    Create ERT members cycling through FIRE → MEDICAL → POLICE → IT.
    First member of each department is ADMIN, rest are STAFF.
    Returns list of Row-like objects and the first FIRE ADMIN token info.
    """
    pw_hash = hash_password(DEFAULT_PASSWORD)
    dept_cycle = [Department.FIRE, Department.MEDICAL, Department.POLICE, Department.IT]
    dept_count: Dict[Department, int] = {d: 0 for d in dept_cycle}

    class _Row:
        def __init__(self, **kw): self.__dict__.update(kw)

    members: List[_Row] = []
    for i in range(count):
        dept  = dept_cycle[i % len(dept_cycle)]
        didx  = dept_count[dept]
        role  = EmergencyTeamRole.ADMIN if didx == 0 else EmergencyTeamRole.STAFF
        name  = _make_ert_name(dept, didx)
        phone = f"+353871{200000 + i:06d}"
        email_safe = name.lower().replace(" ", ".").replace("'", "")
        email = f"{email_safe}@drs.ie"
        mid   = _uid()

        await db.execute(text("""
            INSERT INTO emergency_teams (
                id, phone_number, password_hash, full_name, email,
                role, department, status, created_at, updated_at
            ) VALUES (
                :id, :phone, :pw, :name, :email,
                CAST(:role AS emergency_team_role),
                CAST(:dept AS department),
                CAST(:status AS user_status),
                :now, :now
            )
        """), {
            "id": mid, "phone": phone, "pw": pw_hash, "name": name,
            "email": email, "role": role.value,
            "dept": dept.value, "status": UserStatus.ACTIVE.name, "now": now,
        })
        members.append(_Row(id=mid, phone_number=phone, full_name=name,
                            role=role, department=dept))
        dept_count[dept] += 1

    await db.flush()
    logger.info(f"[dev/seed] created {len(members)} ERT members")
    return members


async def _seed_users(db: AsyncSession, count: int, now: datetime):
    """Create citizen users with sequentially generated Irish names."""
    class _Row:
        def __init__(self, **kw): self.__dict__.update(kw)

    citizens: List[_Row] = []
    for i in range(count):
        first = _FIRST[i % len(_FIRST)]
        last  = _LAST[(i * 3) % len(_LAST)]
        name  = f"{first} {last}"
        phone = f"+353851{100000 + i:06d}"
        email = f"{first.lower()}.{last.lower().replace(chr(39), '')}_{i}@citizen.drs"
        cid   = _uid()

        await db.execute(text("""
            INSERT INTO users (
                id, phone_number, full_name, email,
                role, status, created_at, updated_at
            ) VALUES (
                :id, :phone, :name, :email,
                CAST(:role AS user_role),
                CAST(:status AS user_status),
                :now, :now
            )
        """), {
            "id": cid, "phone": phone, "name": name, "email": email,
            "role": UserRole.RESIDENT.name, "status": UserStatus.ACTIVE.name,
            "now": now,
        })
        citizens.append(_Row(id=cid, phone_number=phone, full_name=name))

    await db.flush()
    logger.info(f"[dev/seed] created {len(citizens)} citizens")
    return citizens


async def _seed_units(db: AsyncSession, count: int, max_crew: int,
                      ert_members: List, now: datetime) -> Dict[str, int]:
    """
    Create emergency units (cycling through UNIT_STATION_POOL) and assign crew.
    Returns {'units': N, 'crew_assignments': M}.
    """
    class _Row:
        def __init__(self, **kw): self.__dict__.update(kw)

    # Build dept→members lookup
    dept_members: Dict[Department, List] = {d: [] for d in Department}
    for m in ert_members:
        dept_members[m.department].append(m)
    dept_crew_idx: Dict[Department, int] = {d: 0 for d in Department}

    units_created  = 0
    crew_assigned  = 0

    for i in range(count):
        stn  = UNIT_STATION_POOL[i % len(UNIT_STATION_POOL)]
        uid  = _uid()
        code = f"UNIT-{stn['utype'].name[:3]}-{i+1:03d}"
        name = f"{stn['utype'].name.replace('_', ' ').title()} {i+1} — {stn['city']}"

        await db.execute(text("""
            INSERT INTO emergency_units (
                id, unit_code, unit_name, unit_type, department,
                station_name, station_location,
                unit_status, capacity, total_deployments,
                created_at, updated_at
            ) VALUES (
                :id, :code, :name,
                CAST(:utype AS unit_type),
                CAST(:dept  AS department),
                :station_name,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                CAST(:status AS unit_status),
                :capacity, 0, :now, :now
            )
        """), {
            "id": uid, "code": code, "name": name,
            "utype": stn["utype"].name,
            "dept":  stn["dept"].value,
            "station_name": stn["name"],
            "lon": stn["lon"], "lat": stn["lat"],
            "status": UnitStatus.AVAILABLE.name,
            "capacity": max_crew, "now": now,
        })
        units_created += 1

        # Assign up to max_crew members from matching department
        dept      = stn["dept"]
        pool      = dept_members.get(dept, [])
        if pool:
            for _ in range(min(max_crew, len(pool))):
                m = pool[dept_crew_idx[dept] % len(pool)]
                dept_crew_idx[dept] += 1
                try:
                    await db.execute(text("""
                        INSERT INTO unit_crew (unit_id, team_member_id)
                        VALUES (:uid, :mid)
                        ON CONFLICT DO NOTHING
                    """), {"uid": uid, "mid": m.id})
                    crew_assigned += 1
                except Exception:
                    pass

    await db.flush()
    logger.info(f"[dev/seed] created {units_created} units, {crew_assigned} crew assignments")
    return {"units": units_created, "crew_assignments": crew_assigned}


async def _seed_disasters(
    db: AsyncSession,
    count: int,
    with_reroutes: int,
    with_evacuations: int,
    reports_per_cluster: int,
    ert_members: List,
    citizens: List,
    now: datetime,
) -> Dict[str, int]:
    """
    Seed disaster data:
    • Indices 0 … with_evacuations-1  → PENDING reports (CRITICAL, Dublin)  → evacuation
    • Indices 5 … 5+with_reroutes-1   → PENDING reports (HIGH, road_blocked) → reroute
    • Remaining up to count           → direct ACTIVE insert (no pipeline)
    """
    # Clamp
    with_evacuations = min(with_evacuations, 5)
    with_reroutes    = min(with_reroutes, 5)
    count            = min(count, len(DISASTER_SCENARIOS))

    # Find a default assigned_to_id (first FIRE ADMIN, or any ERT, or None)
    assigned_id = None
    for m in ert_members:
        if m.department == Department.FIRE and m.role == EmergencyTeamRole.ADMIN:
            assigned_id = m.id
            break
    if not assigned_id and ert_members:
        assigned_id = ert_members[0].id

    citizen_idx  = 0
    reports_made = 0
    direct_made  = 0

    CORROBORATING = [
        "I can confirm this incident near {addr}. Situation is deteriorating rapidly.",
        "Witnessed the emergency at {addr}. Urgent multi-agency response required.",
        "Multiple casualties visible at {addr}. Please deploy all available units.",
        "Infrastructure damage visible at {addr}. Roads blocked in both directions.",
    ]

    def _jitter():
        return (random.random() - 0.5) * 0.004  # ±220 m

    def _citizen():
        nonlocal citizen_idx
        if not citizens:
            return None
        c = citizens[citizen_idx % len(citizens)]
        citizen_idx += 1
        return c

    for idx in range(count):
        sc = DISASTER_SCENARIOS[idx]

        # ── Pipeline disasters (PENDING reports) ──────────────────────────────
        is_evac   = idx < with_evacuations
        is_route  = (5 <= idx < 5 + with_reroutes)
        use_pipeline = is_evac or is_route

        if use_pipeline:
            # Lead report — 30+ min old so evaluation loop processes it first
            lead_c = _citizen()
            await db.execute(text("""
                INSERT INTO disaster_reports (
                    id, user_id, disaster_type, severity, description,
                    location, location_address,
                    people_affected, multiple_casualties, structural_damage, road_blocked,
                    report_status, created_at, updated_at
                ) VALUES (
                    :id, :user_id,
                    CAST(:dtype    AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    :description,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                    :address, :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    CAST('PENDING' AS disaster_report_status),
                    :created_at, :updated_at
                )
            """), {
                "id":                  _uid(),
                "user_id":             lead_c.id if lead_c else None,
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
                "created_at":          now - timedelta(minutes=35),
                "updated_at":          now,
            })
            reports_made += 1

            # Corroborating reports — 1–8 min old → become DUPLICATE after lead processed
            for j in range(reports_per_cluster - 1):
                c = _citizen()
                corr_sev = (
                    sc["severity"].value if j == 0
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
                        :id, :user_id,
                        CAST(:dtype    AS disaster_type),
                        CAST(:severity AS disaster_severity),
                        :description,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                        :address, :people_affected,
                        :multiple_casualties, :structural_damage, :road_blocked,
                        CAST('PENDING' AS disaster_report_status),
                        :created_at, :updated_at
                    )
                """), {
                    "id":                  _uid(),
                    "user_id":             c.id if c else None,
                    "dtype":               sc["type"].value,
                    "severity":            corr_sev,
                    "description":         CORROBORATING[j % len(CORROBORATING)].format(addr=sc["address"]),
                    "lon":                 sc["lon"] + _jitter(),
                    "lat":                 sc["lat"] + _jitter(),
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
                reports_made += 1

        else:
            # ── Direct ACTIVE insert (skip pipeline) ───────────────────────────
            did        = _uid()
            tracking   = f"DRS-{now.strftime('%Y%m%d')}-{idx+1:04d}"
            await db.execute(text("""
                INSERT INTO disasters (
                    id, created_at, updated_at,
                    tracking_id, type, severity, disaster_status,
                    location, location_address, affected_area,
                    description, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    assigned_to_id, assigned_department,
                    response_time, resolved_time, resolution_notes,
                    created_by_id, disaster_metadata
                ) VALUES (
                    :id, :created_at, :updated_at,
                    :tracking_id,
                    CAST(:type     AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    CAST(:dstatus  AS disaster_status),
                    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                    :location_address, NULL,
                    :description, :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    :assigned_to_id, CAST(:assigned_department AS department),
                    NULL, NULL, NULL,
                    :created_by_id, NULL
                )
            """), {
                "id":                  did,
                "created_at":          now - timedelta(minutes=random.randint(10, 180)),
                "updated_at":          now,
                "tracking_id":         tracking,
                "type":                sc["type"].value,
                "severity":            sc["severity"].value,
                "dstatus":             "ACTIVE",
                "longitude":           sc["lon"],
                "latitude":            sc["lat"],
                "location_address":    sc["address"],
                "description":         sc["description"],
                "people_affected":     sc["people_affected"],
                "multiple_casualties": sc["multiple_casualties"],
                "structural_damage":   sc["structural_damage"],
                "road_blocked":        sc["road_blocked"],
                "assigned_to_id":      assigned_id,
                "assigned_department": sc["department"].value,
                "created_by_id":       assigned_id,
            })
            direct_made += 1

    await db.flush()
    pipeline_count = count - direct_made
    logger.info(
        f"[dev/seed] disasters: {pipeline_count} via pipeline ({reports_made} PENDING reports), "
        f"{direct_made} direct ACTIVE inserts"
    )
    return {
        "pipeline_disasters": pipeline_count,
        "direct_active":      direct_made,
        "pending_reports":    reports_made,
    }


async def _seed_trips(db: AsyncSession, count: int, citizens: List, now: datetime) -> int:
    """
    Create active trips centred on live disasters found in the DB.
    Falls back to the first 5 DISASTER_SCENARIOS if no disasters are seeded yet.

    Each trip has:
    - current_lat/lng  within ~1.5 km of disaster (gets picked up by 2 km reroute radius)
    - dest_lat/lng     ~17-22 km on the OPPOSITE side (creates long, visually clear route)
    """
    # Query active disaster locations from the DB
    result = await db.execute(text("""
        SELECT
            ST_Y(location::geometry) AS lat,
            ST_X(location::geometry) AS lon
        FROM disasters
        WHERE disaster_status = 'ACTIVE'
        ORDER BY created_at DESC
        LIMIT 10
    """))
    rows = result.fetchall()

    if rows:
        disaster_locs = [{"lat": r.lat, "lon": r.lon} for r in rows]
    else:
        # No active disasters yet — use hardcoded pipeline disaster locations
        # (pipeline disasters are PENDING reports at this point)
        disaster_locs = [
            {"lat": sc["lat"], "lon": sc["lon"]}
            for sc in DISASTER_SCENARIOS[:8]
            if sc["road_blocked"]
        ][:5]

    if not disaster_locs:
        disaster_locs = [{"lat": 53.3498, "lon": -6.2603}]  # O'Connell St fallback

    n_locs = len(disaster_locs)
    trips_per_loc = max(1, count // n_locs)
    offsets_per_loc = LONG_TRIP_OFFSETS

    trip_count = 0
    citizen_idx = 0

    for loc_i, loc in enumerate(disaster_locs):
        for j in range(trips_per_loc):
            if trip_count >= count:
                break
            if not citizens:
                break

            offset = offsets_per_loc[j % len(offsets_per_loc)]
            cdlat, cdlon, ddlat, ddlon = offset

            cur_lat = loc["lat"] + cdlat
            cur_lng = loc["lon"] + cdlon
            dst_lat = loc["lat"] + ddlat
            dst_lng = loc["lon"] + ddlon

            c = citizens[citizen_idx % len(citizens)]
            citizen_idx += 1

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
                "dest_lat":     dst_lat,
                "dest_lng":     dst_lng,
                "vehicle_type": random.choice(VEHICLE_TYPES),
                "expires_at":   now + timedelta(hours=6),
                "created_at":   now,
                "updated_at":   now,
            })
            trip_count += 1

    # Fill remainder if trips_per_loc × n_locs < count
    for i in range(trip_count, count):
        if not citizens:
            break
        loc    = disaster_locs[i % n_locs]
        offset = offsets_per_loc[i % len(offsets_per_loc)]
        cdlat, cdlon, ddlat, ddlon = offset
        c = citizens[citizen_idx % len(citizens)]
        citizen_idx += 1

        await db.execute(text("""
            INSERT INTO active_trips (
                id, user_id, current_lat, current_lng,
                dest_lat, dest_lng, vehicle_type,
                expires_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, :clat, :clng, :dlat, :dlng, :vtype,
                :exp, :now, :now
            )
            ON CONFLICT (user_id) DO UPDATE SET
                current_lat = EXCLUDED.current_lat, current_lng = EXCLUDED.current_lng,
                dest_lat    = EXCLUDED.dest_lat,    dest_lng    = EXCLUDED.dest_lng,
                vehicle_type = EXCLUDED.vehicle_type, expires_at = EXCLUDED.expires_at,
                updated_at  = EXCLUDED.updated_at
        """), {
            "id": _uid(), "user_id": c.id,
            "clat": loc["lat"] + cdlat, "clng": loc["lon"] + cdlon,
            "dlat": loc["lat"] + ddlat, "dlng": loc["lon"] + ddlon,
            "vtype": random.choice(VEHICLE_TYPES),
            "exp": now + timedelta(hours=6), "now": now,
        })
        trip_count += 1

    await db.flush()
    logger.info(f"[dev/seed] created {trip_count} active trips across {n_locs} disaster zones")
    return trip_count


async def _get_tokens(db: AsyncSession, now: datetime):
    """
    Query the DB for the first FIRE ADMIN and first citizen to generate tokens.
    Returns dict of tokens + credentials, or empty if no data.
    """
    fire_admin = await db.execute(text("""
        SELECT id, phone_number, full_name FROM emergency_teams
        WHERE department = 'FIRE' AND role = 'ADMIN'
        ORDER BY created_at LIMIT 1
    """))
    fa = fire_admin.fetchone()

    med_admin = await db.execute(text("""
        SELECT id, phone_number, full_name FROM emergency_teams
        WHERE department = 'MEDICAL' AND role = 'ADMIN'
        ORDER BY created_at LIMIT 1
    """))
    ma = med_admin.fetchone()

    police_admin = await db.execute(text("""
        SELECT id, phone_number, full_name FROM emergency_teams
        WHERE department = 'POLICE' AND role = 'ADMIN'
        ORDER BY created_at LIMIT 1
    """))
    pa = police_admin.fetchone()

    citizen = await db.execute(text("""
        SELECT id, phone_number, full_name FROM users
        ORDER BY created_at LIMIT 1
    """))
    cit = citizen.fetchone()

    tokens = {}
    creds  = {"password": DEFAULT_PASSWORD}
    if fa:
        tokens["fire_admin_token"]   = create_access_token(str(fa.id),  "emergency_team")
        creds["fire_admin_phone"]    = fa.phone_number
        creds["fire_admin_name"]     = fa.full_name
    if ma:
        tokens["med_admin_token"]    = create_access_token(str(ma.id),  "emergency_team")
        creds["med_admin_phone"]     = ma.phone_number
    if pa:
        tokens["police_admin_token"] = create_access_token(str(pa.id),  "emergency_team")
        creds["police_admin_phone"]  = pa.phone_number
    if cit:
        tokens["citizen_token"]      = create_access_token(str(cit.id), "user")
        creds["citizen_phone"]       = cit.phone_number
        creds["citizen_name"]        = cit.full_name
    return tokens, creds


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

# ── Reset ─────────────────────────────────────────────────────────────────────

@router.post("/dev/reset",
             summary="[DEV] Wipe ALL tables (alias for /dev/seed/reset)")
@router.post("/dev/seed/reset",
             summary="[DEV] Wipe ALL tables without re-seeding")
async def reset_database(db: AsyncSession = Depends(get_db)):
    """
    Deletes all rows from every table in FK-safe order.
    Does NOT recreate any test data — use the individual seed endpoints afterwards.
    """
    result = await _wipe(db)
    await db.commit()
    return {
        "message": "All tables wiped.",
        "cleared": result["cleared"],
        "skipped": result["skipped"],
    }


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.post("/dev/seed/teams",
             summary="[DEV] Seed ERT members")
async def seed_teams(
    body: TeamsRequest = Body(default_factory=TeamsRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Create N ERT members cycling through FIRE → MEDICAL → POLICE → IT departments.
    First member of each department is ADMIN; the rest are STAFF.
    Does NOT wipe existing data — safe to call multiple times.
    """
    now = datetime.utcnow()
    members = await _seed_teams(db, body.count, now)
    tokens, creds = await _get_tokens(db, now)
    await db.commit()

    dept_summary = {}
    for m in members:
        k = m.department.value
        dept_summary[k] = dept_summary.get(k, {"admin": 0, "staff": 0})
        if m.role == EmergencyTeamRole.ADMIN:
            dept_summary[k]["admin"] += 1
        else:
            dept_summary[k]["staff"] += 1

    return {
        "message": f"Created {len(members)} ERT members.",
        "count": len(members),
        "by_department": dept_summary,
        "tokens": tokens,
        "credentials": creds,
    }


# ── Users ─────────────────────────────────────────────────────────────────────

@router.post("/dev/seed/users",
             summary="[DEV] Seed citizen users")
async def seed_users(
    body: UsersRequest = Body(default_factory=UsersRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Create N citizen users with generated Irish names and sequential phone numbers.
    Does NOT wipe existing data.
    """
    now = datetime.utcnow()
    citizens = await _seed_users(db, body.count, now)
    tokens, creds = await _get_tokens(db, now)
    await db.commit()
    return {
        "message": f"Created {len(citizens)} citizen users.",
        "count": len(citizens),
        "tokens": tokens,
        "credentials": creds,
    }


# ── Units ─────────────────────────────────────────────────────────────────────

@router.post("/dev/seed/units",
             summary="[DEV] Seed emergency units")
async def seed_units(
    body: UnitsRequest = Body(default_factory=UnitsRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Create N emergency units from a pool of real Irish station locations spread
    across Dublin, Cork, Galway, Limerick, Waterford, Kerry, Sligo and Donegal.

    `max_crew` sets **capacity** for every unit AND is used to assign that many
    crew members per unit from existing ERT members (matched by department).
    If no ERT members exist yet, units are created with no crew.

    Does NOT wipe existing data.
    """
    now = datetime.utcnow()

    # Fetch existing ERT members to assign as crew
    result = await db.execute(text("""
        SELECT id, department, role FROM emergency_teams
        WHERE status = 'ACTIVE'
    """))

    class _M:
        def __init__(self, r):
            self.id         = r.id
            self.department = Department(r.department)
            self.role       = EmergencyTeamRole(r.role)

    ert = [_M(r) for r in result.fetchall()]

    stats = await _seed_units(db, body.count, body.max_crew, ert, now)
    await db.commit()

    return {
        "message": f"Created {stats['units']} units (capacity={body.max_crew}), "
                   f"{stats['crew_assignments']} crew assignments.",
        "units_created":      stats["units"],
        "crew_assignments":   stats["crew_assignments"],
        "capacity_per_unit":  body.max_crew,
        "station_pool_size":  len(UNIT_STATION_POOL),
    }


# ── Disasters ─────────────────────────────────────────────────────────────────

@router.post("/dev/seed/disasters",
             summary="[DEV] Seed disaster scenarios")
async def seed_disasters(
    body: DisastersRequest = Body(default_factory=DisastersRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Seed disaster data using 20 pre-defined Ireland-wide scenarios.

    **Pipeline disasters** (creates PENDING reports → evaluation loop acts on them):

    • `with_evacuations` (default 4) — Dublin CRITICAL disasters. The evaluation loop
      will create ACTIVE disasters, dispatch units, and trigger evacuation plans.
      Lead report is 35 min old so it gets processed first.

    • `with_reroutes` (default 5) — Ireland-wide HIGH disasters with road_blocked=True.
      The evaluation loop will create ACTIVE disasters, dispatch units, and trigger
      reroute plans.

    **Direct ACTIVE** — remaining `count - with_evacuations - with_reroutes` scenarios
    are inserted directly as ACTIVE disasters (no reroute/evacuation plan).

    ⚠️  Evacuation plans are only supported for Dublin disasters (shelters are
    hardcoded to Dublin infrastructure in evacuation_service.py).
    """
    now = datetime.utcnow()

    # Fetch existing ERT + citizens for report authorship
    ert_result = await db.execute(text("""
        SELECT id, department, role FROM emergency_teams WHERE status = 'ACTIVE'
    """))
    class _E:
        def __init__(self, r):
            self.id         = r.id
            self.department = Department(r.department)
            self.role       = EmergencyTeamRole(r.role)
    ert = [_E(r) for r in ert_result.fetchall()]

    cit_result = await db.execute(text("""
        SELECT id, phone_number FROM users ORDER BY created_at
    """))
    class _C:
        def __init__(self, r):
            self.id           = r.id
            self.phone_number = r.phone_number
    citizens = [_C(r) for r in cit_result.fetchall()]

    if not citizens:
        # Auto-seed minimal citizens if none exist
        citizens = await _seed_users(db, 10, now)

    stats = await _seed_disasters(
        db,
        count               = body.count,
        with_reroutes       = body.with_reroutes,
        with_evacuations    = body.with_evacuations,
        reports_per_cluster = body.reports_per_cluster,
        ert_members         = ert,
        citizens            = citizens,
        now                 = now,
    )
    await db.commit()

    pipeline_n = stats["pipeline_disasters"]
    direct_n   = stats["direct_active"]
    return {
        "message": (
            f"Seeded {body.count} disasters: {pipeline_n} via pipeline "
            f"({stats['pending_reports']} PENDING reports, "
            f"evaluation loop will activate them within 60 s), "
            f"{direct_n} direct ACTIVE."
        ),
        "total_scenarios_seeded": body.count,
        "pipeline": {
            "with_evacuations":    body.with_evacuations,
            "with_reroutes":       body.with_reroutes,
            "pending_reports":     stats["pending_reports"],
            "lead_reports":        pipeline_n,
        },
        "direct_active": direct_n,
        "what_happens_next": {
            "evaluation_loop": "Runs every 60 s in FastAPI lifespan",
            "lead_report":     "35 min old → processed first → ACTIVE disaster created",
            "reroute":         "road_blocked=True + HIGH/CRITICAL → reroute plan created",
            "evacuation":      "CRITICAL + Dublin → evacuation plan triggered",
            "corroborating":   "1–8 min old → DUPLICATE (nearby ACTIVE disaster)",
        },
    }


# ── Trips ─────────────────────────────────────────────────────────────────────

@router.post("/dev/seed/trips",
             summary="[DEV] Seed active trips crossing disaster zones")
async def seed_trips(
    body: TripsRequest = Body(default_factory=TripsRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Create N active trips spread across all **ACTIVE** disasters found in the DB,
    falling back to the pipeline disaster locations if no ACTIVE disasters exist yet.

    Each trip is ≥ 20 km end-to-end with the vehicle's current position within
    1.5 km of the disaster centre (so the 2 km reroute radius always finds it)
    and the destination 17–22 km on the opposite side.

    6 cardinal/diagonal crossing offsets are cycled per disaster location to
    maximise visual variety on the map.

    Seeding trips AFTER disasters is recommended so the live disaster locations
    from the DB can be used automatically.
    """
    now = datetime.utcnow()

    # Fetch citizens to assign trips to
    cit_result = await db.execute(text("""
        SELECT id, phone_number FROM users ORDER BY created_at
    """))
    class _C:
        def __init__(self, r):
            self.id = r.id
            self.phone_number = r.phone_number

    citizens = [_C(r) for r in cit_result.fetchall()]
    if not citizens:
        citizens = await _seed_users(db, body.count + 10, now)

    trip_count = await _seed_trips(db, body.count, citizens, now)
    await db.commit()

    return {
        "message": f"Created {trip_count} active trips crossing disaster zones.",
        "trips_created": trip_count,
        "trip_length_km": "≈ 20–28 km end-to-end",
        "current_position": "within 1.5 km of disaster centre (2 km reroute radius)",
        "destination":      "17–22 km on opposite side of disaster",
    }


# ── All (convenience wrapper) ──────────────────────────────────────────────────

@router.post("/dev/seed/all",
             summary="[DEV] Wipe + seed everything in one call")
async def seed_all(
    body: AllRequest = Body(default_factory=AllRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Convenience endpoint: wipes ALL tables then seeds teams → users → units →
    disasters → trips in the correct dependency order.

    All sub-requests use their own defaults if not supplied.  Example:

    ```json
    {
      "teams":     { "count": 12 },
      "users":     { "count": 50 },
      "units":     { "count": 20, "max_crew": 4 },
      "disasters": { "count": 20, "with_reroutes": 5, "with_evacuations": 4 },
      "trips":     { "count": 30 }
    }
    ```

    Returns JWT tokens ready for use in Postman / Swagger.
    """
    now = datetime.utcnow()

    # 1. Wipe
    await _wipe(db)

    # 2. ERT
    members = await _seed_teams(db, body.teams.count, now)

    # 3. Citizens
    citizens = await _seed_users(db, body.users.count, now)

    # 4. Units
    unit_stats = await _seed_units(
        db, body.units.count, body.units.max_crew, members, now
    )

    # 5. Disasters
    d = body.disasters
    disaster_stats = await _seed_disasters(
        db,
        count               = d.count,
        with_reroutes       = d.with_reroutes,
        with_evacuations    = d.with_evacuations,
        reports_per_cluster = d.reports_per_cluster,
        ert_members         = members,
        citizens            = citizens,
        now                 = now,
    )

    # 6. Trips — read disasters from DB (direct ACTIVE ones are already committed
    #    via flush; pipeline ones are PENDING so we fall back to scenario coords)
    trip_count = await _seed_trips(db, body.trips.count, citizens, now)

    await db.commit()

    tokens, creds = await _get_tokens(db, now)

    pipeline_n = disaster_stats["pipeline_disasters"]
    direct_n   = disaster_stats["direct_active"]

    return {
        "message": (
            f"Full seed complete — {len(members)} ERT, {len(citizens)} citizens, "
            f"{unit_stats['units']} units, "
            f"{d.count} disasters ({pipeline_n} pipeline + {direct_n} direct ACTIVE), "
            f"{trip_count} trips."
        ),
        "summary": {
            "ert_members":           len(members),
            "citizens":              len(citizens),
            "emergency_units":       unit_stats["units"],
            "crew_assignments":      unit_stats["crew_assignments"],
            "capacity_per_unit":     body.units.max_crew,
            "direct_active_disasters":    direct_n,
            "pipeline_disasters":    pipeline_n,
            "pending_reports":       disaster_stats["pending_reports"],
            "active_trips":          trip_count,
        },
        "tokens": tokens,
        "credentials": creds,
        "what_happens_next": {
            "step_1": "Evaluation loop runs every 60 s (FastAPI lifespan asyncio task)",
            "step_2": f"Lead reports (35 min old) → {pipeline_n} ACTIVE disasters created",
            "step_3": "DirectCoordinationClient dispatches nearest AVAILABLE units",
            "step_4": f"{d.with_reroutes} reroute plans created (road_blocked=True + HIGH/CRITICAL)",
            "step_5": f"{d.with_evacuations} evacuation plans triggered (CRITICAL + Dublin)",
            "step_6": "Corroborating reports → DUPLICATE (nearby ACTIVE + age < 15 min)",
            "monitor": "kubectl logs -n drs -l app=drs-backend -f  (or your pod label)",
        },
        "ireland_coverage": {
            "dublin":      "8 disasters (evac + reroute scenarios + direct ACTIVE)",
            "cork":        "3 disasters",
            "galway":      "2 disasters",
            "limerick":    "2 disasters",
            "waterford":   "1 disaster",
            "kerry":       "1 disaster",
            "sligo":       "1 disaster",
            "donegal":     "1 disaster",
            "wicklow":     "1 disaster",
        },
    }


# ── Legacy /dev/seed (backward compat → delegates to /dev/seed/all defaults) ──

@router.post("/dev/seed",
             summary="[DEV] Legacy: full wipe + seed with defaults")
async def seed_legacy(db: AsyncSession = Depends(get_db)):
    """
    Backward-compatible single-call seed.  Equivalent to POST /dev/seed/all
    with all default parameters.  Prefer /dev/seed/all for parameterized control.
    """
    return await seed_all(AllRequest(), db)
