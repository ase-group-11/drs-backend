# app/api/v1/dev_seed.py
"""
DEV-ONLY: Full database seed for end-to-end demo and testing.

POST /api/v1/dev/seed
  1. Wipes ALL tables (FK-safe order)
  2. Creates 20 ERT team members across Fire/Medical/Police/IT departments
  3. Creates 100 citizen users
  4. Creates 60 emergency units spread across Ireland (all valid UnitType values)
  5. Creates 50 verified active disasters spread across Ireland — one type
     per location, realistic for Irish geography
  6. Creates 400 disaster reports (8 per disaster: 1 VERIFIED + 1 DUPLICATE +
     6 PENDING, from the 100 citizens)
  7. Creates 50 deployments — one per disaster, status DISPATCHED
  8. Creates active_trips for 30 citizens (for reroute/evacuation services)
  9. Returns JWT tokens for fire ADMIN + citizen ready for immediate use

All coordinates are real Irish locations verified against OSM / Google Maps.
All enum values match app/db/models/enums.py exactly.

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

from fastapi import APIRouter, Depends
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
# Real Irish station / base coordinates (verified OSM)
# ─────────────────────────────────────────────────────────────────────────────

FIRE_STATIONS = [
    {"name": "Tara Street Fire Station",        "lat": 53.3451, "lon": -6.2592, "city": "Dublin"},
    {"name": "Phibsborough Fire Station",        "lat": 53.3630, "lon": -6.2749, "city": "Dublin"},
    {"name": "Finglas Fire Station",             "lat": 53.3839, "lon": -6.2944, "city": "Dublin"},
    {"name": "Dún Laoghaire Fire Station",       "lat": 53.2942, "lon": -6.1357, "city": "Dún Laoghaire"},
    {"name": "Tallaght Fire Station",            "lat": 53.2875, "lon": -6.3612, "city": "Tallaght"},
    {"name": "Cork City Fire Brigade HQ",        "lat": 51.8985, "lon": -8.4714, "city": "Cork"},
    {"name": "Galway Fire Station",              "lat": 53.2743, "lon": -9.0488, "city": "Galway"},
    {"name": "Limerick Fire Station",            "lat": 52.6638, "lon": -8.6267, "city": "Limerick"},
    {"name": "Waterford Fire Station",           "lat": 52.2593, "lon": -7.1102, "city": "Waterford"},
    {"name": "Sligo Fire Station",               "lat": 54.2766, "lon": -8.4761, "city": "Sligo"},
    {"name": "Kilkenny Fire Station",            "lat": 52.6541, "lon": -7.2448, "city": "Kilkenny"},
    {"name": "Athlone Fire Station",             "lat": 53.4228, "lon": -7.9408, "city": "Athlone"},
]

AMBULANCE_STATIONS = [
    {"name": "St James's Hospital, Dublin",      "lat": 53.3414, "lon": -6.2928, "city": "Dublin"},
    {"name": "Beaumont Hospital, Dublin",         "lat": 53.3906, "lon": -6.2386, "city": "Dublin"},
    {"name": "Tallaght University Hospital",      "lat": 53.2875, "lon": -6.3782, "city": "Dublin"},
    {"name": "Cork University Hospital",          "lat": 51.8948, "lon": -8.4855, "city": "Cork"},
    {"name": "University Hospital Galway",        "lat": 53.2821, "lon": -9.0601, "city": "Galway"},
    {"name": "University Hospital Limerick",      "lat": 52.6814, "lon": -8.6282, "city": "Limerick"},
    {"name": "University Hospital Waterford",     "lat": 52.2386, "lon": -7.0862, "city": "Waterford"},
    {"name": "Sligo University Hospital",         "lat": 54.2736, "lon": -8.4972, "city": "Sligo"},
    {"name": "Letterkenny University Hospital",   "lat": 54.9534, "lon": -7.7205, "city": "Donegal"},
    {"name": "Mayo University Hospital",          "lat": 53.8581, "lon": -9.2988, "city": "Castlebar"},
]

GARDA_STATIONS = [
    {"name": "Pearse Street Garda Station",      "lat": 53.3444, "lon": -6.2482, "city": "Dublin"},
    {"name": "Store Street Garda Station",        "lat": 53.3497, "lon": -6.2471, "city": "Dublin"},
    {"name": "Rathmines Garda Station",           "lat": 53.3237, "lon": -6.2658, "city": "Dublin"},
    {"name": "Blanchardstown Garda Station",      "lat": 53.3874, "lon": -6.3762, "city": "Dublin"},
    {"name": "Cork Garda Divisional HQ",          "lat": 51.9012, "lon": -8.4637, "city": "Cork"},
    {"name": "Galway Garda Station",              "lat": 53.2712, "lon": -9.0529, "city": "Galway"},
    {"name": "Limerick Garda Station",            "lat": 52.6692, "lon": -8.6325, "city": "Limerick"},
    {"name": "Waterford Garda Station",           "lat": 52.2581, "lon": -7.1136, "city": "Waterford"},
]

# ─────────────────────────────────────────────────────────────────────────────
# 50 realistic Irish disaster locations — one disaster type per location
# Each dict: location, type, severity, description, flags, affected population
# Coordinates are precise real Irish locations
# ─────────────────────────────────────────────────────────────────────────────

DISASTER_SCENARIOS = [
    # Dublin inner city
    {
        "lat": 53.3441, "lon": -6.2675, "address": "O'Connell Street, Dublin 1",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": "Large fire in a multi-storey commercial building. Smoke visible across city centre. Multiple floors involved, evacuation in progress.",
        "people_affected": 320, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3498, "lon": -6.2295, "address": "Grand Canal Dock, Dublin 2",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.CRITICAL,
        "description": "Severe flooding in Docklands area following storm surge. Water levels rising rapidly, ground floors submerged, roads impassable.",
        "people_affected": 890, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3350, "lon": -6.2620, "address": "St Stephen's Green, Dublin 2",
        "type": DisasterType.STORM, "severity": DisasterSeverity.MEDIUM,
        "description": "Severe windstorm causing widespread tree falls and structural damage. Multiple roads blocked by fallen trees.",
        "people_affected": 150, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3303, "lon": -6.2488, "address": "Rathmines Road, Dublin 6",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.MEDIUM,
        "description": "Residential house fire spreading to adjacent terraced properties. Three houses affected.",
        "people_affected": 18, "multiple_casualties": False, "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3600, "lon": -6.2488, "address": "Drumcondra Road, Dublin 9",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "River Tolka burst its banks, flooding residential streets. Dozens of homes have water entering ground floors.",
        "people_affected": 420, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.2875, "lon": -6.3612, "address": "Tallaght Town Centre, Dublin 24",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.CRITICAL,
        "description": "Major fire in shopping centre. Roof structure at risk of collapse. Mass evacuation of shopping centre and surrounding blocks underway.",
        "people_affected": 1200, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3943, "lon": -6.3985, "address": "Blanchardstown Retail Park, Dublin 15",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": "Warehouse fire with hazardous chemicals involved. Toxic smoke plume visible. Exclusion zone established.",
        "people_affected": 95, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.2942, "lon": -6.1357, "address": "Dún Laoghaire Pier, Co. Dublin",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Storm surge causing flooding along seafront. High waves overtopping pier, coastal road flooded.",
        "people_affected": 240, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Cork
    {
        "lat": 51.8985, "lon": -8.4639, "address": "Patrick Street, Cork City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "River Lee flooding Cork city centre. Main shopping street has 60cm of water. Businesses and residences severely affected.",
        "people_affected": 1400, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 51.8948, "lon": -8.5123, "address": "Sunday's Well, Cork",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": "Fire in Victorian terrace houses. Three properties fully involved. Risk of spread to adjacent structures.",
        "people_affected": 32, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 51.9012, "lon": -8.4637, "address": "MacCurtain Street, Cork",
        "type": DisasterType.STORM, "severity": DisasterSeverity.MEDIUM,
        "description": "Storm damage to building facades. Falling masonry causing road closure. Several vehicles damaged.",
        "people_affected": 60, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Galway
    {
        "lat": 53.2743, "lon": -9.0488, "address": "Shop Street, Galway City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.CRITICAL,
        "description": "Corrib flooding causing severe inundation of city centre. Entire pedestrian areas submerged. Emergency services overwhelmed.",
        "people_affected": 2200, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3059, "lon": -8.9789, "address": "Oranmore, Co. Galway",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Hurricane-force winds causing widespread structural damage. Multiple buildings with roof failure.",
        "people_affected": 180, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.2619, "lon": -9.0686, "address": "Salthill Promenade, Galway",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Storm surge battering Salthill promenade. Coastal flooding extending 200m inland.",
        "people_affected": 310, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Limerick
    {
        "lat": 52.6638, "lon": -8.6267, "address": "O'Connell Street, Limerick City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Shannon flooding central Limerick. Ground floors of businesses and apartments submerged.",
        "people_affected": 780, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.6814, "lon": -8.5871, "address": "Castletroy, Limerick",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.MEDIUM,
        "description": "Industrial unit fire in Castletroy business park. No casualties but significant structural damage.",
        "people_affected": 45, "multiple_casualties": False, "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    # Waterford
    {
        "lat": 52.2593, "lon": -7.1102, "address": "The Quay, Waterford City",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Suir flooding along Waterford quays. Water lapping onto quayside road, businesses evacuating.",
        "people_affected": 490, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.2501, "lon": -7.1205, "address": "Tramore Road, Waterford",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.MEDIUM,
        "description": "Car dealership fire. Multiple vehicles alight. Risk to adjacent residential properties.",
        "people_affected": 20, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Sligo
    {
        "lat": 54.2766, "lon": -8.4761, "address": "Wine Street, Sligo Town",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Garavogue River bursting banks. Town centre flooding. Roads cut off, residents stranded.",
        "people_affected": 620, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 54.2619, "lon": -8.5016, "address": "Strandhill, Co. Sligo",
        "type": DisasterType.STORM, "severity": DisasterSeverity.CRITICAL,
        "description": "Extreme Atlantic storm. 30m waves reported. Coastal properties destroyed. Emergency evacuation of 200 homes.",
        "people_affected": 560, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Donegal
    {
        "lat": 54.9534, "lon": -7.7205, "address": "Letterkenny Town Centre, Co. Donegal",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Swilly flooding lower town. Several streets impassable. Residential basements flooded.",
        "people_affected": 280, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 55.0527, "lon": -8.2332, "address": "Bundoran, Co. Donegal",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Severe Atlantic storm. Cliff collapse onto coastal road. Tourist area heavily affected.",
        "people_affected": 130, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Mayo
    {
        "lat": 53.8581, "lon": -9.2988, "address": "Castlebar Town Centre, Co. Mayo",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Castlebar River burst banks causing flooding across main street and residential areas.",
        "people_affected": 340, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.7671, "lon": -9.6605, "address": "Westport, Co. Mayo",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Storm damage in Westport town. Multiple trees down, power outages, roof damage to hotel.",
        "people_affected": 200, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Wicklow / Wexford
    {
        "lat": 52.9800, "lon": -6.0450, "address": "Wicklow Town Harbour",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Storm surge overwhelming harbour walls. Three fishing vessels sunk. Harbour area flooded.",
        "people_affected": 95, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.3369, "lon": -6.4633, "address": "Wexford Town Centre",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Slaney estuary flooding town centre streets. Businesses at ground floor level affected.",
        "people_affected": 380, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Kerry
    {
        "lat": 52.0602, "lon": -9.5033, "address": "Tralee Town Centre, Co. Kerry",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Lee floods Tralee town centre. Widespread residential and commercial flooding.",
        "people_affected": 510, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.1661, "lon": -10.0020, "address": "Dingle, Co. Kerry",
        "type": DisasterType.STORM, "severity": DisasterSeverity.CRITICAL,
        "description": "Ex-hurricane storm causing catastrophic damage to Dingle peninsula. Infrastructure severely damaged.",
        "people_affected": 420, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Tipperary / Kilkenny
    {
        "lat": 52.6541, "lon": -7.2448, "address": "Kilkenny City Centre",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": "Fire in medieval building in Kilkenny city. Heritage structures at significant risk. Extensive suppression operation.",
        "people_affected": 75, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.4735, "lon": -8.1618, "address": "Thurles, Co. Tipperary",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Suir river flooding fields and approaching residential areas. Preventive evacuation in progress.",
        "people_affected": 160, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Leinster / Midlands
    {
        "lat": 53.4228, "lon": -7.9408, "address": "Athlone Town Centre",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.CRITICAL,
        "description": "Shannon flooding reaching record levels in Athlone. Entire west bank of town flooded. Military deployed.",
        "people_affected": 1800, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.5242, "lon": -6.4594, "address": "Navan, Co. Meath",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Boyne flooding Navan town. Several residential estates with water entering homes.",
        "people_affected": 390, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.6985, "lon": -6.3742, "address": "Drogheda, Co. Louth",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Severe storm causing widespread structural damage. Industrial estate roofs blown off.",
        "people_affected": 270, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.1424, "lon": -6.9826, "address": "Naas, Co. Kildare",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.MEDIUM,
        "description": "Factory fire in industrial estate. Significant structural damage. No casualties.",
        "people_affected": 40, "multiple_casualties": False, "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 52.8408, "lon": -6.9229, "address": "Carlow Town Centre",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Barrow river flooding low-lying areas of Carlow. Town centre partially affected.",
        "people_affected": 220, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.3369, "lon": -7.7122, "address": "Clonmel, Co. Tipperary",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Suir at record levels through Clonmel. Major floods affecting businesses and homes in the heart of the town.",
        "people_affected": 670, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Ulster
    {
        "lat": 54.1773, "lon": -6.3430, "address": "Dundalk, Co. Louth",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Violent storm hitting Dundalk. Two cranes collapsed on construction site. Major road closures.",
        "people_affected": 185, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.9997, "lon": -8.4869, "address": "Roscommon Town",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Suck river flooding surrounding farmland and approaching town. Roads cut off.",
        "people_affected": 190, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    # More Dublin suburbs
    {
        "lat": 53.2582, "lon": -6.1339, "address": "Bray, Co. Wicklow",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Major storm hitting Bray seafront. Esplanade flooded, promenade businesses evacuated. DART line affected.",
        "people_affected": 380, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3199, "lon": -6.4442, "address": "Clondalkin, Dublin 22",
        "type": DisasterType.FIRE, "severity": DisasterSeverity.HIGH,
        "description": "Fire in high-density residential block. Five floors affected. Large-scale evacuation.",
        "people_affected": 210, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3806, "lon": -6.5938, "address": "Lucan, Co. Dublin",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Liffey flooding Lucan riverside estates. Preventive evacuations of 80 homes.",
        "people_affected": 260, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.3236, "lon": -6.0970, "address": "Dalkey, Co. Dublin",
        "type": DisasterType.STORM, "severity": DisasterSeverity.MEDIUM,
        "description": "Storm damage on coastal road. Fallen rocks blocking road. Some structural damage to older buildings.",
        "people_affected": 80, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.4034, "lon": -6.1488, "address": "Malahide, Co. Dublin",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Coastal flood surge in Malahide. Estuary overflowing. Dozens of properties flooded.",
        "people_affected": 340, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    # Additional mix
    {
        "lat": 53.5253, "lon": -9.8538, "address": "Clifden, Co. Galway",
        "type": DisasterType.STORM, "severity": DisasterSeverity.CRITICAL,
        "description": "Severe storm in Connemara. Clifden town heavily damaged. Many households isolated, power out across region.",
        "people_affected": 890, "multiple_casualties": True, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 53.0837, "lon": -8.9901, "address": "Gort, Co. Galway",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Turlough flooding across south Galway plains. Multiple roads submerged. Farmland and rural communities cut off.",
        "people_affected": 430, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.8408, "lon": -8.9842, "address": "Ennis, Co. Clare",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Fergus flooding Ennis town. Several streets in town centre submerged to 80cm depth.",
        "people_affected": 560, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 52.7191, "lon": -9.4533, "address": "Kilrush, Co. Clare",
        "type": DisasterType.STORM, "severity": DisasterSeverity.MEDIUM,
        "description": "Storm damage to marina and seafront businesses. Several boats sunk. No casualties.",
        "people_affected": 120, "multiple_casualties": False, "structural_damage": True, "road_blocked": False,
        "department": Department.FIRE,
    },
    {
        "lat": 53.7001, "lon": -7.8007, "address": "Longford Town Centre",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.MEDIUM,
        "description": "Camlin river flooding approach roads to town. Town centre accessible on one route only.",
        "people_affected": 290, "multiple_casualties": False, "structural_damage": False, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 54.1104, "lon": -8.7465, "address": "Ballina, Co. Mayo",
        "type": DisasterType.FLOOD, "severity": DisasterSeverity.HIGH,
        "description": "Moy river flooding Ballina. Quayside inundated. Emergency services deployed at scale.",
        "people_affected": 710, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
    {
        "lat": 54.0032, "lon": -9.5297, "address": "Belmullet, Co. Mayo",
        "type": DisasterType.STORM, "severity": DisasterSeverity.HIGH,
        "description": "Atlantic storm battering Belmullet peninsula. Several houses uninhabitable. Roads to peninsula blocked.",
        "people_affected": 260, "multiple_casualties": False, "structural_damage": True, "road_blocked": True,
        "department": Department.FIRE,
    },
]

# 100 Irish citizen names + phone numbers
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
    ("Aoibhinn Murray",   "+353851000021"), ("Ruairí Sheehan",     "+353851000022"),
    ("Clodagh Nolan",     "+353851000023"), ("Fionn Clarke",       "+353851000024"),
    ("Sinead Lyons",      "+353851000025"), ("Tadhg Moran",        "+353851000026"),
    ("Éadaoin Moore",     "+353851000027"), ("Lorcan Flynn",       "+353851000028"),
    ("Muireann Connolly", "+353851000029"), ("Brian Jordan",       "+353851000030"),
    ("Saoirse Casey",     "+353851000031"), ("Oisín Brady",        "+353851000032"),
    ("Eimear Dunne",      "+353851000033"), ("Fearghal Grant",     "+353851000034"),
    ("Niall Keane",       "+353851000035"), ("Aoife Cunningham",   "+353851000036"),
    ("Cormac Healy",      "+353851000037"), ("Clíona Dempsey",     "+353851000038"),
    ("Rónán Foley",       "+353851000039"), ("Sinéad Harrington",  "+353851000040"),
    ("Cillian Ward",      "+353851000041"), ("Emer Thornton",      "+353851000042"),
    ("Diarmuid Smyth",    "+353851000043"), ("Íde Lawlor",         "+353851000044"),
    ("Fintan Boyle",      "+353851000045"), ("Bríd Whittle",       "+353851000046"),
    ("Ardal Higgins",     "+353851000047"), ("Catriona Power",     "+353851000048"),
    ("Liam O'Riordan",    "+353851000049"), ("Áine Sheridan",      "+353851000050"),
    ("Conal Burke",       "+353851000051"), ("Méabh Stapleton",    "+353851000052"),
    ("Daithí Scanlon",    "+353851000053"), ("Eilis Tobin",        "+353851000054"),
    ("Piaras Collins",    "+353851000055"), ("Blathnaid O'Toole",  "+353851000056"),
    ("Tomás Fallon",      "+353851000057"), ("Attracta Hennessy",  "+353851000058"),
    ("Ultan Delaney",     "+353851000059"), ("Sadhbh Gorman",      "+353851000060"),
    ("Ciarán O'Leary",    "+353851000061"), ("Clodagh Madden",     "+353851000062"),
    ("Séamus Hanlon",     "+353851000063"), ("Úna Regan",          "+353851000064"),
    ("Dónal Moriarty",    "+353851000065"), ("Ita Naughton",       "+353851000066"),
    ("Donncha Coyne",     "+353851000067"), ("Labhaoise Barry",    "+353851000068"),
    ("Micheál Kearney",   "+353851000069"), ("Treasa Costello",    "+353851000070"),
    ("Páraic Dowd",       "+353851000071"), ("Honora Durkan",      "+353851000072"),
    ("Colm Maguire",      "+353851000073"), ("Sorcha McDermott",   "+353851000074"),
    ("Peadar Dunphy",     "+353851000075"), ("Fionnuala Staunton", "+353851000076"),
    ("Eoghan Tierney",    "+353851000077"), ("Eibhlín Monaghan",   "+353851000078"),
    ("Cormac Gaffney",    "+353851000079"), ("Bríona Flanagan",    "+353851000080"),
    ("Fiachra Carroll",   "+353851000081"), ("Nuala Begley",       "+353851000082"),
    ("Padraig Langan",    "+353851000083"), ("Fionnula O'Callaghan","+353851000084"),
    ("Seosaimhín Burke",  "+353851000085"), ("Caolán Redmond",     "+353851000086"),
    ("Bláithín Coffey",   "+353851000087"), ("Aindrias Fortune",   "+353851000088"),
    ("Finnbarra Carthy",  "+353851000089"), ("Gobnait Roche",      "+353851000090"),
    ("Setanta Whelan",    "+353851000091"), ("Meadhbh Callaghan",  "+353851000092"),
    ("Odhran Purcell",    "+353851000093"), ("Rónán Wickham",      "+353851000094"),
    ("Muirne O'Sullivan", "+353851000095"), ("Tiarnán Quigley",    "+353851000096"),
    ("Lasairfhíona Daly", "+353851000097"), ("Diarmuid Corkery",   "+353851000098"),
    ("Bríd Kinahan",      "+353851000099"), ("Oisín Muldoon",      "+353851000100"),
]

# ERT team members: 30 members across all departments
ERT_TEAM_DATA = [
    # FIRE — 8 members
    ("Cdr James Brennan",          "+353871100001", "jbrennan@drs.ie",    EmergencyTeamRole.ADMIN,   Department.FIRE),
    ("Stn Off Mary Doyle",         "+353871100002", "mdoyle@drs.ie",      EmergencyTeamRole.MANAGER, Department.FIRE),
    ("FF Patrick O'Brien",         "+353871100003", "pobrien@drs.ie",     EmergencyTeamRole.STAFF,   Department.FIRE),
    ("FF Sinéad Walsh",            "+353871100004", "swalsh@drs.ie",      EmergencyTeamRole.STAFF,   Department.FIRE),
    ("FF Ciarán Murphy",           "+353871100005", "cmurphy@drs.ie",     EmergencyTeamRole.STAFF,   Department.FIRE),
    ("FF Aoife Kelly",             "+353871100006", "akelly@drs.ie",      EmergencyTeamRole.STAFF,   Department.FIRE),
    ("FF Séamus Flanagan",         "+353871100021", "sflanagan@drs.ie",   EmergencyTeamRole.STAFF,   Department.FIRE),
    ("FF Niamh Connolly",          "+353871100022", "nconnolly@drs.ie",   EmergencyTeamRole.STAFF,   Department.FIRE),
    # MEDICAL — 8 members  (index 8–15)
    ("Dr Fiona Ryan",              "+353871100007", "fryan@drs.ie",       EmergencyTeamRole.ADMIN,   Department.MEDICAL),
    ("Para Mgr Seán Quinn",        "+353871100008", "squinn@drs.ie",      EmergencyTeamRole.MANAGER, Department.MEDICAL),
    ("Paramedic Niamh Lee",        "+353871100009", "nlee@drs.ie",        EmergencyTeamRole.STAFF,   Department.MEDICAL),
    ("Paramedic Oisín Brady",      "+353871100010", "obrady@drs.ie",      EmergencyTeamRole.STAFF,   Department.MEDICAL),
    ("EMT Laura Byrne",            "+353871100011", "lbyrne@drs.ie",      EmergencyTeamRole.STAFF,   Department.MEDICAL),
    ("EMT Eoin Farrell",           "+353871100012", "efarrell@drs.ie",    EmergencyTeamRole.STAFF,   Department.MEDICAL),
    ("EMT Sorcha Hennessy",        "+353871100023", "shennessy@drs.ie",   EmergencyTeamRole.STAFF,   Department.MEDICAL),
    ("Paramedic Cillian Ó'Neill",  "+353871100024", "coneill@drs.ie",     EmergencyTeamRole.STAFF,   Department.MEDICAL),
    # POLICE — 8 members  (index 16–23)
    ("Supt Claire O'Connor",       "+353871100013", "coconnor@drs.ie",    EmergencyTeamRole.ADMIN,   Department.POLICE),
    ("Insp Brian Gallagher",       "+353871100014", "bgallagher@drs.ie",  EmergencyTeamRole.MANAGER, Department.POLICE),
    ("Sgt Orla Doherty",           "+353871100015", "odoherty@drs.ie",    EmergencyTeamRole.STAFF,   Department.POLICE),
    ("Garda Tomás Nolan",          "+353871100016", "tnolan@drs.ie",      EmergencyTeamRole.STAFF,   Department.POLICE),
    ("Garda Roisín Clarke",        "+353871100017", "rclarke@drs.ie",     EmergencyTeamRole.STAFF,   Department.POLICE),
    ("Garda Declan Moran",         "+353871100018", "dmoran@drs.ie",      EmergencyTeamRole.STAFF,   Department.POLICE),
    ("Garda Aoibhinn Mac Aleenan", "+353871100025", "amacaleenan@drs.ie", EmergencyTeamRole.STAFF,   Department.POLICE),
    ("Garda Pádraig Ó'Sullivan",   "+353871100026", "posullivan@drs.ie",  EmergencyTeamRole.STAFF,   Department.POLICE),
    # IT — 4 members  (index 24–27)
    ("IT Dir Ciara Higgins",       "+353871100019", "chiggins@drs.ie",    EmergencyTeamRole.ADMIN,   Department.IT),
    ("SysEng Liam Sheridan",       "+353871100020", "lsheridan@drs.ie",   EmergencyTeamRole.STAFF,   Department.IT),
    ("DevOps Eilís Ní Mhurchú",    "+353871100027", "emurchu@drs.ie",     EmergencyTeamRole.STAFF,   Department.IT),
    ("Data Analyst Conor Keane",   "+353871100028", "ckeane@drs.ie",      EmergencyTeamRole.STAFF,   Department.IT),
    # RESCUE COORDINATION — 2 members  (index 28–29)
    ("Rescue Coord Áine Burke",    "+353871100029", "aburke@drs.ie",      EmergencyTeamRole.ADMIN,   Department.FIRE),
    ("Rescue Off Ruairí McGrath",  "+353871100030", "rmcgrath@drs.ie",    EmergencyTeamRole.STAFF,   Department.FIRE),
]



def _uid() -> str:
    return str(uuid.uuid4())



# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/dev/seed", summary="[DEV] Full DB wipe and realistic Ireland seed")
async def seed_full_database(db: AsyncSession = Depends(get_db)):
    """
    Wipes ALL tables and seeds realistic demo data for all use-cases.

    Idempotent — safe to call repeatedly.
    Returns JWT tokens for immediate Swagger/Postman use.
    """
    now = datetime.utcnow()

    # ── STEP 1: Wipe everything (FK-safe order) ───────────────────────────────
    wipe_tables = [
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
            await db.execute(text(f"DELETE FROM {tbl}"))
            logger.debug(f"[dev/seed] cleared {tbl}")
        except Exception as exc:
            logger.warning(f"[dev/seed] could not clear {tbl}: {exc}")

    await db.flush()

    # ── STEP 2: Create ERT team members (raw SQL — avoids insertmanyvalues) ─────
    pw_hash = hash_password(TEAM_PASSWORD)

    # Simple dataclass-like objects so the rest of the code can read .id / .phone_number
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
            "status": UserStatus.ACTIVE.value, "now": now,
        })
        ert_members.append(_Row(id=mid, phone_number=phone, full_name=full_name))

    logger.info(f"[dev/seed] created {len(ert_members)} ERT members")

    # Convenience refs (indices match ERT_TEAM_DATA order above)
    fire_admin   = ert_members[0]   # Cdr James Brennan — FIRE ADMIN
    med_admin    = ert_members[8]   # Dr Fiona Ryan     — MEDICAL ADMIN
    police_admin = ert_members[16]  # Supt Claire O'Connor — POLICE ADMIN

    # ── STEP 3: Create 100 citizen users (raw SQL) ────────────────────────────
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
            "role": UserRole.RESIDENT.value, "status": UserStatus.ACTIVE.value,
            "now": now,
        })
        citizens.append(_Row(id=cid, phone_number=phone, full_name=full_name))

    logger.info(f"[dev/seed] created {len(citizens)} citizens")

    # ── STEP 4: Create 60 emergency units across Ireland ─────────────────────
    # Distribution: 20 fire engines, 14 ambulances, 10 patrol cars,
    # 6 rapid response, 6 rescue, 4 command = 62 total (no HazMat)
    unit_specs = []

    # Fire engines — one per fire station
    for i, stn in enumerate(FIRE_STATIONS):
        unit_specs.append({
            "code": f"UNIT-FIRE-{i+1:03d}",
            "name": f"Fire Engine {i+1} — {stn['city']}",
            "type": UnitType.FIRE_ENGINE,
            "dept": Department.FIRE,
            "station": stn,
        })

    # Ambulances — one per ambulance station
    for i, stn in enumerate(AMBULANCE_STATIONS):
        unit_specs.append({
            "code": f"UNIT-AMB-{i+1:03d}",
            "name": f"Ambulance {i+1} — {stn['city']}",
            "type": UnitType.AMBULANCE,
            "dept": Department.MEDICAL,
            "station": stn,
        })

    # Patrol cars — 10 units (from Garda stations)
    for i, stn in enumerate(GARDA_STATIONS):
        unit_specs.append({
            "code": f"UNIT-POL-{i+1:03d}",
            "name": f"Patrol Car {i+1} — {stn['city']}",
            "type": UnitType.PATROL_CAR,
            "dept": Department.POLICE,
            "station": stn,
        })
    # Two more patrol cars using first two Garda stations
    for i in range(2):
        stn = GARDA_STATIONS[i]
        unit_specs.append({
            "code": f"UNIT-POL-{i+9:03d}",
            "name": f"Patrol Car {i+9} — {stn['city']}",
            "type": UnitType.PATROL_CAR,
            "dept": Department.POLICE,
            "station": stn,
        })

    # Rapid response — 6 units (mix of locations)
    rapid_stations = [
        {"name": "Dublin City Rapid Response",   "lat": 53.3488, "lon": -6.2607, "city": "Dublin"},
        {"name": "Cork Rapid Response Unit",      "lat": 51.8985, "lon": -8.4714, "city": "Cork"},
        {"name": "Galway Rapid Response Unit",    "lat": 53.2743, "lon": -9.0488, "city": "Galway"},
        {"name": "Limerick Rapid Response Unit",  "lat": 52.6638, "lon": -8.6267, "city": "Limerick"},
        {"name": "Waterford Rapid Response Unit", "lat": 52.2593, "lon": -7.1102, "city": "Waterford"},
        {"name": "Sligo Rapid Response Unit",     "lat": 54.2766, "lon": -8.4761, "city": "Sligo"},
    ]
    for i, stn in enumerate(rapid_stations):
        unit_specs.append({
            "code": f"UNIT-RR-{i+1:03d}",
            "name": f"Rapid Response {i+1} — {stn['city']}",
            "type": UnitType.RAPID_RESPONSE,
            "dept": Department.FIRE,
            "station": stn,
        })

    # Rescue — 6 units (expanded; no HazMat)
    rescue_stations = [
        {"name": "Dublin Mountain Rescue",     "lat": 53.2441, "lon": -6.3877, "city": "Dublin"},
        {"name": "Galway Coastal Rescue",      "lat": 53.2619, "lon": -9.0686, "city": "Galway"},
        {"name": "Kerry Mountain Rescue",      "lat": 52.0602, "lon": -9.5033, "city": "Kerry"},
        {"name": "Cork Water Rescue",          "lat": 51.8903, "lon": -8.4731, "city": "Cork"},
        {"name": "Wicklow Mountain Rescue",    "lat": 52.9769, "lon": -6.3669, "city": "Wicklow"},
        {"name": "Donegal Sea Rescue",         "lat": 54.6542, "lon": -8.1090, "city": "Donegal"},
    ]
    for i, stn in enumerate(rescue_stations):
        unit_specs.append({
            "code": f"UNIT-RES-{i+1:03d}",
            "name": f"Rescue Unit {i+1} — {stn['city']}",
            "type": UnitType.RESCUE,
            "dept": Department.FIRE,
            "station": stn,
        })

    # Command — 4 units (expanded)
    command_stations = [
        {"name": "Dublin Mobile Command",        "lat": 53.3488, "lon": -6.2607, "city": "Dublin"},
        {"name": "Cork Regional Command",        "lat": 51.8985, "lon": -8.4714, "city": "Cork"},
        {"name": "Limerick Regional Command",    "lat": 52.6638, "lon": -8.6267, "city": "Limerick"},
        {"name": "National Command Support",     "lat": 53.2743, "lon": -9.0488, "city": "Galway"},
    ]
    for i, stn in enumerate(command_stations):
        unit_specs.append({
            "code": f"UNIT-CMD-{i+1:03d}",
            "name": f"Command Vehicle {i+1} — {stn['city']}",
            "type": UnitType.COMMAND,
            "dept": Department.FIRE,
            "station": stn,
        })

    # Raw SQL insert — avoids insertmanyvalues sentinel mismatch on UUID columns
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
            "utype": spec["type"].value, "dept": spec["dept"].value,
            "station_name": stn["name"],
            "lon": stn["lon"], "lat": stn["lat"],
            "status": UnitStatus.AVAILABLE.value,
            "capacity": 4, "total_deployments": 0,
            "now": now,
        })
        emergency_units.append(_Row(id=uid, unit_type=spec["type"], unit_code=spec["code"]))

    logger.info(f"[dev/seed] created {len(emergency_units)} emergency units")

    # ── STEP 5: Create 400 PENDING disaster reports (8 per location cluster) ────
    #
    # NO disasters, deployments, reroute plans, or evacuation plans are created
    # here. The Celery task (process_pending_reports, runs every 30s) will:
    #   • Evaluate each PENDING report through the rules-engine + XGBoost pipeline
    #   • Create an ACTIVE disaster for the first high-confidence report at each loc
    #   • Mark subsequent reports at the same location DUPLICATE
    #   • Automatically dispatch nearest AVAILABLE units (DirectCoordinationClient)
    #   • Trigger reroute for reports with road_blocked=True or severity >= HIGH
    #     (HttpRerouteClient → POST /api/v1/reroute/trigger)
    #   • Trigger evacuation for CRITICAL severity disasters
    #     (EvacuationService.plan_evacuation → activate_evacuation)
    #
    # Each cluster of 8 reports at the same location will result in:
    #   - Report 1 processed first → new disaster created, pipeline triggered
    #   - Reports 2-8 processed → flagged DUPLICATE (disaster now exists nearby)

    citizen_idx = 0
    report_count = 0

    CORROBORATE_DESCRIPTIONS = [
        "I can confirm this incident near {addr}. Situation is serious and worsening.",
        "Witnessed the emergency at {addr}. Urgent response needed.",
        "Multiple people are affected at {addr}. Please send help immediately.",
        "Emergency ongoing at {addr}. Roads are blocked and people need assistance.",
        "Still happening at {addr}. Smoke/water/damage visible from my location.",
        "Confirming report from {addr}. Emergency services not yet on scene.",
        "Serious incident at {addr}. I have seen injuries and structural damage.",
    ]

    def jitter():
        """Random coordinate offset ±300m to simulate nearby-but-distinct reporters."""
        return (random.random() - 0.5) * 0.006

    for sc in DISASTER_SCENARIOS:
        # Lead report — exact coordinates, full severity, road_blocked as per scenario.
        # This will be the first report the Celery task evaluates; it creates the disaster.
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
                CAST('pending' AS disaster_report_status),
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
            "created_at":          now - timedelta(minutes=random.randint(2, 15)),
            "updated_at":          now,
        })
        report_count += 1

        # 7 corroborating PENDING reports — slight coordinate jitter, same location cluster.
        # The Celery task processes these after the lead report has already created a disaster,
        # so they get flagged as DUPLICATE by the evaluation service.
        for j in range(7):
            c = citizens[citizen_idx % len(citizens)]
            citizen_idx += 1
            desc = CORROBORATE_DESCRIPTIONS[j].format(addr=sc["address"])
            # Slightly lower severity on later reports — realistic variation
            corr_severity = (
                sc["severity"].value if j < 3
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
                    CAST('pending' AS disaster_report_status),
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
                    max(2, sc["people_affected"] // 2)
                ),
                "multiple_casualties": sc["multiple_casualties"],
                "structural_damage":   sc["structural_damage"] and (j < 5),
                "road_blocked":        sc["road_blocked"],
                "created_at":          now - timedelta(minutes=random.randint(1, 20)),
                "updated_at":          now,
            })
            report_count += 1

    await db.flush()
    logger.info(
        "[dev/seed] created %d PENDING reports across %d location clusters — "
        "awaiting Celery evaluation pipeline",
        report_count, len(DISASTER_SCENARIOS),
    )

    # ── STEP 6: Create 30 active trips (for reroute/evacuation services) ──────

    # Citizens travelling near disaster zones
    trip_count = 0
    for i in range(30):
        c     = citizens[i]
        sc    = DISASTER_SCENARIOS[i % len(DISASTER_SCENARIOS)]
        # Current position slightly away from disaster, destination near it
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
            "id":          _uid(),
            "user_id":     c.id,
            "current_lat": sc["lat"] + (random.random() - 0.5) * 0.05,
            "current_lng": sc["lon"] + (random.random() - 0.5) * 0.05,
            "dest_lat":    sc["lat"] + (random.random() - 0.5) * 0.02,
            "dest_lng":    sc["lon"] + (random.random() - 0.5) * 0.02,
            "vehicle_type": random.choice(["general", "general", "public_transport"]),
            "expires_at":  now + timedelta(hours=4),
            "created_at":  now,
            "updated_at":  now,
        })
        trip_count += 1

    await db.flush()
    logger.info(f"[dev/seed] created {trip_count} active trips")

    # ── STEP 7: Commit everything ─────────────────────────────────────────────
    await db.commit()
    logger.info("[dev/seed] committed all seed data")

    # ── STEP 8: Generate tokens ───────────────────────────────────────────────
    fire_admin_token   = create_access_token(fire_admin.id,   "emergency_team")
    med_admin_token    = create_access_token(med_admin.id,    "emergency_team")
    police_admin_token = create_access_token(police_admin.id, "emergency_team")
    citizen_token      = create_access_token(citizens[0].id,  "user")

    return {
        "message": (
            "Seed complete — all tables reset. "
            f"{report_count} PENDING reports across {len(DISASTER_SCENARIOS)} location clusters seeded. "
            "The Celery evaluation task (runs every 30s) will evaluate each report, "
            "create active disasters where threshold is met, auto-dispatch nearest units, "
            "trigger reroute (road_blocked or HIGH+ severity), "
            "and trigger evacuation plans (CRITICAL severity)."
        ),
        "summary": {
            "ert_members":     len(ert_members),
            "citizens":        len(citizens),
            "emergency_units": len(emergency_units),
            "pending_reports": report_count,
            "location_clusters": len(DISASTER_SCENARIOS),
            "active_trips":    trip_count,
            "active_disasters": 0,
            "deployments":     0,
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
            "step_1": "Celery worker processes PENDING reports every 30s (up to 50 per batch)",
            "step_2": "First report per cluster → disaster created → units dispatched automatically",
            "step_3": "Remaining 7 reports per cluster → flagged DUPLICATE (disaster already exists)",
            "step_4": "road_blocked=True or severity>=HIGH → reroute triggered automatically",
            "step_5": "CRITICAL severity disasters → evacuation plan created + activated automatically",
            "monitor": "kubectl logs -n drs -l app=drs-celery-worker -f",
        },
        "note": (
            "Use fire_admin_token as Bearer for ERT/admin endpoints. "
            "citizen_token for citizen endpoints. "
            "Re-call POST /dev/seed at any time for a fresh clean slate."
        ),
    }
