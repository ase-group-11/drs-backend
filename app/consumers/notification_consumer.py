# # File: app/consumers/notification_consumer.py
# """
# Notification Consumer - 3 queues, 3 threads, targeted geo delivery.
# notification_queue, reroute_queue, coordination_queue.
# Python 3.9 compatible.
# """
# import json, logging, os, sys, threading
# from datetime import datetime, timezone
# from typing import Any, Dict, List, Optional, Set

# sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# from dotenv import load_dotenv; load_dotenv()

# import redis as sync_redis
# from app.consumers.base_consumer import BaseConsumer
# from app.core.config import settings
# from app.services.alert_channels import build_html, send_email, send_sms, should_send_external
# from app.services.location_registry import get_disaster_subscribers, is_user_online, queue_offline_alert, resolve_targets

# logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
# logger = logging.getLogger("notification_consumer")

# REDIS_URL = settings.REDIS_URL
# REDIS_CHANNEL = "app_alerts"
# _redis: Optional[sync_redis.Redis] = None

# def _get_redis():
#     global _redis
#     if _redis is None:
#         _redis = sync_redis.from_url(REDIS_URL, decode_responses=True)
#     return _redis

# SEVERITY_COLOUR = {"CRITICAL":"red","HIGH":"orange","MEDIUM":"yellow","LOW":"blue","INFO":"green"}
# RADIUS_KM = {"CRITICAL":3.0,"HIGH":2.0,"MEDIUM":1.0,"LOW":0.5,"INFO":0.5}

# def _latlon(d):
#     loc = d.get("location") or {}
#     return (loc.get("lat"), loc.get("lon")) if isinstance(loc, dict) else (None, None)

# def _deliver(service, event_type, severity, title, message, data,
#              disaster_id=None, disaster_lat=None, disaster_lon=None,
#              radius_km=None, broadcast=False,
#              citizen_phone=None, citizen_email=None,
#              team_phone=None, team_email=None, location=""):

#     tracking_id = data.get("tracking_id", "")

#     if broadcast or not disaster_id:
#         target_ids = None
#     elif disaster_lat is not None and disaster_lon is not None:
#         r = radius_km or RADIUS_KM.get(severity.upper(), 1.0)
#         target_ids = resolve_targets(disaster_id, disaster_lat, disaster_lon, r)
#     else:
#         target_ids = get_disaster_subscribers(disaster_id)

#     alert = {
#         "service": service, "event_type": event_type, "severity": severity,
#         "colour": SEVERITY_COLOUR.get(severity.upper(), "blue"),
#         "timestamp": datetime.now(timezone.utc).isoformat(),
#         "title": title, "message": message, "data": data,
#         "target_user_ids": list(target_ids) if target_ids is not None else None,
#     }

#     try:
#         _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
#         cnt = "broadcast" if target_ids is None else str(len(target_ids))
#         logger.info(f"[{event_type}] -> {cnt} user(s)")
#     except Exception as exc:
#         logger.error(f"Redis publish failed: {exc}")

#     if target_ids is not None:
#         offline = {k: v for k, v in alert.items() if k != "target_user_ids"}
#         for uid in target_ids:
#             if not is_user_online(uid):
#                 queue_offline_alert(uid, offline)

#     if not should_send_external(severity):
#         return
#     sms = f"[DRS] {title}\n{message}\nRef: {tracking_id}"
#     html = build_html(title, message, tracking_id, severity, location)
#     subj = f"[DRS Alert] {title}"
#     for phone in filter(None, [citizen_phone, team_phone]):
#         send_sms(phone, sms)
#     for addr in filter(None, [citizen_email, team_email]):
#         send_email(addr, subj, sms, html)


# class DisasterNotificationConsumer(BaseConsumer):
#     """notification_queue - full disaster lifecycle."""
#     def __init__(self):
#         super().__init__(queue_name="notification_queue")

#     def process_message(self, data):
#         handlers = {
#             "disaster.dispatched": self._dispatched,
#             "disaster.verified": self._verified,
#             "disaster.updated": self._updated,
#             "disaster.resolved": self._resolved,
#             "disaster.backup_requested": self._backup_requested,
#             "disaster.unit_completed": self._unit_completed,
#         }
#         h = handlers.get(data.get("event_type", ""))
#         if h: h(data)
#         else: logger.warning(f"[notification_queue] unknown: {data.get('event_type')}")

#     def _dispatched(self, d):
#         lat, lon = _latlon(d); units = d.get("units_dispatched", 0); pri = d.get("priority_level", "STANDARD")
#         _deliver("disaster","disaster.dispatched","HIGH",
#                  f"Units dispatched - {d.get('tracking_id','')}",
#                  f"{units} unit(s) en route with {pri} priority.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "units_dispatched":units,"priority_level":pri},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
#                  citizen_phone=d.get("citizen_phone"), citizen_email=d.get("citizen_email"),
#                  team_phone=d.get("team_phone"), team_email=d.get("team_email"),
#                  location=d.get("location_address",""))

#     def _verified(self, d):
#         lat, lon = _latlon(d)
#         _deliver("disaster","disaster.verified","HIGH",
#                  f"Disaster confirmed on-scene - {d.get('tracking_id','')}",
#                  f"Emergency unit on-scene. Situation: {d.get('situation_report','N/A')}",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "verified_by_unit":d.get("verified_by_unit"),"situation_report":d.get("situation_report")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
#                  citizen_phone=d.get("citizen_phone"), citizen_email=d.get("citizen_email"),
#                  team_phone=d.get("team_phone"), team_email=d.get("team_email"),
#                  location=d.get("location_address",""))

#     def _updated(self, d):
#         lat, lon = _latlon(d)
#         _deliver("disaster","disaster.updated","LOW",
#                  f"Disaster update - {d.get('tracking_id','')}",
#                  d.get("details","Status updated."),
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "update_type":d.get("update_type"),"details":d.get("details")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon)

#     def _resolved(self, d):
#         lat, lon = _latlon(d)
#         _deliver("disaster","disaster.resolved","INFO",
#                  f"Disaster resolved - {d.get('tracking_id','')}",
#                  d.get("resolution_notes","Situation resolved."),
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "resolution_notes":d.get("resolution_notes"),"resolved_time":d.get("resolved_time")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon)

#     def _backup_requested(self, d):
#         lat, lon = _latlon(d); resources = d.get("resources_needed") or ["Unspecified"]
#         _deliver("disaster","disaster.backup_requested","CRITICAL",
#                  f"URGENT: Backup needed - {d.get('tracking_id','')}",
#                  f"Field unit needs backup. Resources: {', '.join(resources)}",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "requesting_unit":d.get("requesting_unit"),"resources_needed":resources},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
#                  team_phone=d.get("team_phone"), team_email=d.get("team_email"),
#                  location=d.get("location_address",""))

#     def _unit_completed(self, d):
#         lat, lon = _latlon(d)
#         _deliver("disaster","disaster.unit_completed","INFO",
#                  f"Unit complete - {d.get('tracking_id','')}",
#                  "A response unit has completed its mission.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),"unit_id":d.get("unit_id")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon)


# class RerouteNotificationConsumer(BaseConsumer):
#     """
#     reroute_queue - road impact / clearance alerts.
#     Uses 3km radius so nearby road users get notified.
#     User near disaster A gets disaster A road alerts only.
#     """
#     def __init__(self):
#         super().__init__(queue_name="reroute_queue")

#     def process_message(self, data):
#         handlers = {
#             "disaster.verified": self._road_impact,
#             "disaster.resolved": self._roads_restored,
#             "disaster.unit_completed": self._partial_clearance,
#         }
#         h = handlers.get(data.get("event_type",""))
#         if h: h(data)
#         else: logger.warning(f"[reroute_queue] unknown: {data.get('event_type')}")

#     def _road_impact(self, d):
#         lat, lon = _latlon(d); dtype = d.get("type","disaster").lower()
#         _deliver("reroute","reroute.road_impact_alert","HIGH",
#                  f"Road impact alert - {d.get('tracking_id','')}",
#                  f"A {dtype} confirmed nearby. Expect road disruptions. Check live map for routes.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "disaster_type":d.get("type"),"severity":d.get("severity"),
#                   "location_address":d.get("location_address")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
#                  radius_km=3.0,
#                  citizen_phone=d.get("citizen_phone"), citizen_email=d.get("citizen_email"),
#                  location=d.get("location_address",""))

#     def _roads_restored(self, d):
#         lat, lon = _latlon(d)
#         _deliver("reroute","reroute.roads_restored","INFO",
#                  f"Roads restored - {d.get('tracking_id','')}",
#                  "Emergency ended. Normal traffic flow resuming.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon, radius_km=3.0)

#     def _partial_clearance(self, d):
#         lat, lon = _latlon(d)
#         _deliver("reroute","reroute.partial_clearance","INFO",
#                  f"Route update - {d.get('tracking_id','')}",
#                  "An emergency unit completed its mission. Some routes may be clearing.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),"unit_id":d.get("unit_id")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon, radius_km=3.0)


# class CoordinationNotificationConsumer(BaseConsumer):
#     """
#     coordination_queue - team assignment and escalation alerts.
#     Notifies citizen reporter and assigned team directly.
#     """
#     def __init__(self):
#         super().__init__(queue_name="coordination_queue")

#     def process_message(self, data):
#         handlers = {
#             "disaster.verified": self._team_assigned,
#             "disaster.backup_requested": self._escalation,
#         }
#         h = handlers.get(data.get("event_type",""))
#         if h: h(data)
#         else: logger.warning(f"[coordination_queue] unknown: {data.get('event_type')}")

#     def _team_assigned(self, d):
#         lat, lon = _latlon(d); dept = d.get("assigned_department","Emergency services")
#         _deliver("coordination","coordination.team_assigned","HIGH",
#                  f"Response team assigned - {d.get('tracking_id','')}",
#                  f"{dept} assigned to respond. Help is on the way.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "assigned_department":dept,"verified_by_unit":d.get("verified_by_unit")},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
#                  radius_km=1.0,
#                  citizen_phone=d.get("citizen_phone"), citizen_email=d.get("citizen_email"),
#                  team_phone=d.get("team_phone"), team_email=d.get("team_email"),
#                  location=d.get("location_address",""))

#     def _escalation(self, d):
#         lat, lon = _latlon(d); resources = d.get("resources_needed") or ["Unspecified"]
#         _deliver("coordination","coordination.escalation","CRITICAL",
#                  f"URGENT: Escalation - {d.get('tracking_id','')}",
#                  f"Field unit needs resources: {', '.join(resources)}. Coordination action required.",
#                  {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
#                   "requesting_unit":d.get("requesting_unit"),"resources_needed":resources},
#                  disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
#                  radius_km=1.0,
#                  team_phone=d.get("team_phone"), team_email=d.get("team_email"),
#                  location=d.get("location_address",""))


# def _run(consumer):
#     consumer.start()

# if __name__ == "__main__":
#     consumers = [
#         DisasterNotificationConsumer(),
#         RerouteNotificationConsumer(),
#         CoordinationNotificationConsumer(),
#     ]
#     threads = []
#     for c in consumers:
#         t = threading.Thread(target=_run, args=(c,), name=f"thread-{c.queue_name}", daemon=True)
#         threads.append(t)
#         t.start()
#         logger.info(f"Started: {t.name}")
#     for t in threads:
#         t.join()

# File: app/consumers/notification_consumer.py
"""
Notification Consumer - 3 queues, 3 threads, targeted geo delivery.
notification_queue, reroute_queue, coordination_queue.
Python 3.9 compatible.
"""
import json, logging, os, sys, threading
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv; load_dotenv()

import redis as sync_redis
from app.consumers.base_consumer import BaseConsumer
from app.core.config import settings
from app.services.alert_channels import build_html, send_email, send_sms, should_send_external
from app.services.location_registry import get_disaster_subscribers, is_user_online, queue_offline_alert, resolve_targets
from app.api.v1.notifications_ws import get_user_contact

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("notification_consumer")

REDIS_URL = settings.REDIS_URL
REDIS_CHANNEL = "app_alerts"
_redis: Optional[sync_redis.Redis] = None

def _get_redis():
    global _redis
    if _redis is None:
        _redis = sync_redis.from_url(REDIS_URL, decode_responses=True)
    return _redis

# ── Sync DB helper — fetch all active emails from both tables ─
_DB_URL = None
def _get_sync_db_url() -> str:
    """Convert asyncpg DATABASE_URL to psycopg2 format."""
    global _DB_URL
    if _DB_URL is None:
        url = settings.DATABASE_URL
        # asyncpg → psycopg2
        url = url.replace("postgresql+asyncpg://", "postgresql://")
        _DB_URL = url
    return _DB_URL


def _fetch_all_emails() -> List[Dict[str, str]]:
    """
    Query both users and emergency_teams tables for all ACTIVE emails.
    Returns list of {"email": "...", "full_name": "...", "user_type": "..."}
    Called from blocking pika thread — uses psycopg2 (sync), not asyncpg.
    """
    results: List[Dict[str, str]] = []
    try:
        conn = psycopg2.connect(_get_sync_db_url())
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Citizens
        cur.execute("""
            SELECT email, full_name
            FROM users
            WHERE status = 'ACTIVE'
              AND email IS NOT NULL
              AND email != ''
              AND deleted_at IS NULL
        """)
        for row in cur.fetchall():
            results.append({
                "email":     row["email"],
                "full_name": row["full_name"] or "Citizen",
                "user_type": "citizen",
            })

        # Emergency team members
        cur.execute("""
            SELECT email, full_name
            FROM emergency_teams
            WHERE status = 'ACTIVE'
              AND email IS NOT NULL
              AND email != ''
              AND deleted_at IS NULL
        """)
        for row in cur.fetchall():
            results.append({
                "email":     row["email"],
                "full_name": row["full_name"] or "Team Member",
                "user_type": "team",
            })

        cur.close()
        conn.close()
        logger.info(f"_fetch_all_emails: found {len(results)} active addresses")
    except Exception as exc:
        logger.error(f"_fetch_all_emails failed: {exc}")
    return results


SEVERITY_COLOUR = {"CRITICAL":"red","HIGH":"orange","MEDIUM":"yellow","LOW":"blue","INFO":"green"}
RADIUS_KM = {"CRITICAL":3.0,"HIGH":2.0,"MEDIUM":1.0,"LOW":0.5,"INFO":0.5}

def _latlon(d):
    loc = d.get("location") or {}
    return (loc.get("lat"), loc.get("lon")) if isinstance(loc, dict) else (None, None)

def _deliver(service, event_type, severity, title, message, data,
             disaster_id=None, disaster_lat=None, disaster_lon=None,
             radius_km=None, broadcast=False, location="",
             direct_notify_ids: Optional[Set[str]] = None):
    # direct_notify_ids: specific user IDs who must receive SMS+Email
    # regardless of location. Pass the reporter_id, assigned_team_id, etc.
    # Leave None for geo-only targeting.
    """
    1. Resolve target user IDs (geo + direct subscribers)
    2. Publish to Redis pub/sub  →  WebSocket broadcaster delivers to connected clients
    3. Queue alerts for offline users  →  delivered on reconnect
    4. For HIGH/CRITICAL: send SMS + Email to every targeted user using
       their real contact details fetched from the authenticated WS registry.
       If a target user is offline, look them up from the offline queue contact
       cache (not yet implemented) — for now only online users get SMS/Email.
    """
    tracking_id = data.get("tracking_id", "")

    # ── Step 1: Resolve targets ───────────────────────────────
    if broadcast or not disaster_id:
        target_ids: Optional[Set[str]] = None
    elif disaster_lat is not None and disaster_lon is not None:
        r = radius_km or RADIUS_KM.get(severity.upper(), 1.0)
        target_ids = resolve_targets(disaster_id, disaster_lat, disaster_lon, r)
    else:
        target_ids = get_disaster_subscribers(disaster_id)

    # ── Step 2: Build and publish alert envelope ──────────────
    alert = {
        "service": service, "event_type": event_type, "severity": severity,
        "colour": SEVERITY_COLOUR.get(severity.upper(), "blue"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title, "message": message, "data": data,
        "target_user_ids": list(target_ids) if target_ids is not None else None,
    }

    try:
        _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
        cnt = "broadcast" if target_ids is None else str(len(target_ids))
        logger.info(f"[{event_type}] -> {cnt} user(s)")
    except Exception as exc:
        logger.error(f"Redis publish failed: {exc}")

    # ── Step 3: Queue for offline users ───────────────────────
    if target_ids is not None:
        offline = {k: v for k, v in alert.items() if k != "target_user_ids"}
        for uid in target_ids:
            if not is_user_online(uid):
                queue_offline_alert(uid, offline)

    # ── Step 4: Email to ALL active users in DB ──────────────────
    # For every disaster event that warrants external notification
    # (HIGH and CRITICAL), send an email to every active user and
    # team member in the database so they are informed regardless
    # of whether they are connected to the WebSocket.
    #
    # SMS is reserved for CRITICAL only (evacuation scenarios) to
    # avoid cost and noise — email is free and appropriate for all.

    if not should_send_external(severity):
        return  # LOW / INFO / MEDIUM → WebSocket only, no email

    sms_body  = f"[DRS] {title}\n{message}\nRef: {tracking_id}"
    html_body = build_html(title, message, tracking_id, severity, location)
    subject   = f"[DRS Alert] {title}"
    sent_emails: Set[str] = set()

    # ── Email: all users in the database ─────────────────────────
    all_contacts = _fetch_all_emails()
    for contact in all_contacts:
        addr = contact.get("email", "")
        if addr and addr not in sent_emails:
            send_email(addr, subject, sms_body, html_body)
            sent_emails.add(addr)
            logger.info(f"Email sent → {addr} ({contact.get('user_type')})")

    logger.info(f"[{event_type}] Emails sent to {len(sent_emails)} addresses")

    # ── SMS: CRITICAL only → users physically in the impact zone ──
    # (Evacuation alert — only people near the disaster get SMS)
    if severity.upper() == "CRITICAL":
        sent_phones: Set[str] = set()
        geo_targets = target_ids or set()
        for uid in geo_targets:
            contact = get_user_contact(uid)
            phone   = contact.get("phone", "")
            if phone and phone not in sent_phones:
                send_sms(phone, sms_body)
                sent_phones.add(phone)
                logger.info(f"Evacuation SMS → {phone[:7]}*** (user near disaster)")
        logger.info(f"[{event_type}] CRITICAL: SMS sent to {len(sent_phones)} nearby users")


class DisasterNotificationConsumer(BaseConsumer):
    """notification_queue - full disaster lifecycle."""
    def __init__(self):
        super().__init__(queue_name="notification_queue")

    def process_message(self, data):
        handlers = {
            "disaster.dispatched": self._dispatched,
            "disaster.verified": self._verified,
            "disaster.updated": self._updated,
            "disaster.resolved": self._resolved,
            "disaster.backup_requested": self._backup_requested,
            "disaster.unit_completed": self._unit_completed,
        }
        h = handlers.get(data.get("event_type", ""))
        if h: h(data)
        else: logger.warning(f"[notification_queue] unknown: {data.get('event_type')}")

    def _dispatched(self, d):
        lat, lon = _latlon(d); units = d.get("units_dispatched", 0); pri = d.get("priority_level", "STANDARD")
        _deliver("disaster","disaster.dispatched","HIGH",
                 f"Units dispatched - {d.get('tracking_id','')}",
                 f"{units} unit(s) en route with {pri} priority.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "units_dispatched":units,"priority_level":pri},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
                 location=d.get("location_address",""))

    def _verified(self, d):
        lat, lon = _latlon(d)
        # reporter_id: the citizen who submitted this disaster report
        # They deserve a direct SMS/Email confirmation that their report was acted on
        reporter_id = d.get("reporter_id")
        direct = {reporter_id} if reporter_id else None
        _deliver("disaster","disaster.verified","HIGH",
                 f"Disaster confirmed on-scene - {d.get('tracking_id','')}",
                 f"Emergency unit on-scene. Situation: {d.get('situation_report','N/A')}",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "verified_by_unit":d.get("verified_by_unit"),"situation_report":d.get("situation_report")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
                 location=d.get("location_address",""),
                 direct_notify_ids=direct)

    def _updated(self, d):
        lat, lon = _latlon(d)
        _deliver("disaster","disaster.updated","LOW",
                 f"Disaster update - {d.get('tracking_id','')}",
                 d.get("details","Status updated."),
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "update_type":d.get("update_type"),"details":d.get("details")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon)

    def _resolved(self, d):
        lat, lon = _latlon(d)
        _deliver("disaster","disaster.resolved","INFO",
                 f"Disaster resolved - {d.get('tracking_id','')}",
                 d.get("resolution_notes","Situation resolved."),
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "resolution_notes":d.get("resolution_notes"),"resolved_time":d.get("resolved_time")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon)

    def _backup_requested(self, d):
        lat, lon = _latlon(d); resources = d.get("resources_needed") or ["Unspecified"]
        # requesting_unit_user_id: the team member who flagged backup needed
        # coordinator_id: the coordinator who should approve resources
        # Both get direct SMS/Email since this is CRITICAL
        direct = set(filter(None, [
            d.get("requesting_unit_user_id"),   # field responder
            d.get("coordinator_id"),            # coordinator on duty
        ])) or None
        _deliver("disaster","disaster.backup_requested","CRITICAL",
                 f"URGENT: Backup needed - {d.get('tracking_id','')}",
                 f"Field unit needs backup. Resources: {', '.join(resources)}",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "requesting_unit":d.get("requesting_unit"),"resources_needed":resources},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
                 location=d.get("location_address",""),
                 direct_notify_ids=direct)

    def _unit_completed(self, d):
        lat, lon = _latlon(d)
        _deliver("disaster","disaster.unit_completed","INFO",
                 f"Unit complete - {d.get('tracking_id','')}",
                 "A response unit has completed its mission.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),"unit_id":d.get("unit_id")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon)


class RerouteNotificationConsumer(BaseConsumer):
    """
    reroute_queue - road impact / clearance alerts.
    Uses 3km radius so nearby road users get notified.
    User near disaster A gets disaster A road alerts only.
    """
    def __init__(self):
        super().__init__(queue_name="reroute_queue")

    def process_message(self, data):
        handlers = {
            "disaster.verified": self._road_impact,
            "disaster.resolved": self._roads_restored,
            "disaster.unit_completed": self._partial_clearance,
        }
        h = handlers.get(data.get("event_type",""))
        if h: h(data)
        else: logger.warning(f"[reroute_queue] unknown: {data.get('event_type')}")

    def _road_impact(self, d):
        lat, lon = _latlon(d); dtype = d.get("type","disaster").lower()
        _deliver("reroute","reroute.road_impact_alert","HIGH",
                 f"Road impact alert - {d.get('tracking_id','')}",
                 f"A {dtype} confirmed nearby. Expect road disruptions. Check live map for routes.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "disaster_type":d.get("type"),"severity":d.get("severity"),
                  "location_address":d.get("location_address")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
                 radius_km=3.0,
                 location=d.get("location_address",""))

    def _roads_restored(self, d):
        lat, lon = _latlon(d)
        _deliver("reroute","reroute.roads_restored","INFO",
                 f"Roads restored - {d.get('tracking_id','')}",
                 "Emergency ended. Normal traffic flow resuming.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon, radius_km=3.0)

    def _partial_clearance(self, d):
        lat, lon = _latlon(d)
        _deliver("reroute","reroute.partial_clearance","INFO",
                 f"Route update - {d.get('tracking_id','')}",
                 "An emergency unit completed its mission. Some routes may be clearing.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),"unit_id":d.get("unit_id")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon, radius_km=3.0)


class CoordinationNotificationConsumer(BaseConsumer):
    """
    coordination_queue - team assignment and escalation alerts.
    Notifies citizen reporter and assigned team directly.
    """
    def __init__(self):
        super().__init__(queue_name="coordination_queue")

    def process_message(self, data):
        handlers = {
            "disaster.verified": self._team_assigned,
            "disaster.backup_requested": self._escalation,
        }
        h = handlers.get(data.get("event_type",""))
        if h: h(data)
        else: logger.warning(f"[coordination_queue] unknown: {data.get('event_type')}")

    def _team_assigned(self, d):
        lat, lon = _latlon(d); dept = d.get("assigned_department","Emergency services")
        _deliver("coordination","coordination.team_assigned","HIGH",
                 f"Response team assigned - {d.get('tracking_id','')}",
                 f"{dept} assigned to respond. Help is on the way.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "assigned_department":dept,"verified_by_unit":d.get("verified_by_unit")},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
                 radius_km=1.0,
                 location=d.get("location_address",""))

    def _escalation(self, d):
        lat, lon = _latlon(d); resources = d.get("resources_needed") or ["Unspecified"]
        _deliver("coordination","coordination.escalation","CRITICAL",
                 f"URGENT: Escalation - {d.get('tracking_id','')}",
                 f"Field unit needs resources: {', '.join(resources)}. Coordination action required.",
                 {"disaster_id":d.get("disaster_id"),"tracking_id":d.get("tracking_id"),
                  "requesting_unit":d.get("requesting_unit"),"resources_needed":resources},
                 disaster_id=d.get("disaster_id"), disaster_lat=lat, disaster_lon=lon,
                 radius_km=1.0,
                 location=d.get("location_address",""))


def _run(consumer):
    consumer.start()

if __name__ == "__main__":
    consumers = [
        DisasterNotificationConsumer(),
        RerouteNotificationConsumer(),
        CoordinationNotificationConsumer(),
    ]
    threads = []
    for c in consumers:
        t = threading.Thread(target=_run, args=(c,), name=f"thread-{c.queue_name}", daemon=True)
        threads.append(t)
        t.start()
        logger.info(f"Started: {t.name}")
    for t in threads:
        t.join()