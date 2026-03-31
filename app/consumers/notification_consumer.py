"""
app/consumers/notification_consumer.py

Unified Consumer — handles ALL queues in one process, one connection.

Queues consumed:
    notification_queue      — disaster lifecycle events
    notification.reroute    — reroute events
    reroute_queue           — disaster events that trigger rerouting
    evaluation_queue        — disaster.reported events
    coordination_queue      — team assignment and escalation alerts

Run:
    python -m app.consumers.notification_consumer
"""

import os
import sys
import json
import logging
import signal
import time
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from typing import Optional, Set, List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

import pika
import redis as sync_redis

from app.core.config import settings

# ---------------------------------------------------------------------------
# Feature branch imports — graceful fallback if not yet available
# ---------------------------------------------------------------------------
try:
    from app.services.alert_channels import build_html, send_email, send_sms, should_send_external
    from app.services.location_registry import (
        get_disaster_subscribers, is_user_online,
        queue_offline_alert, resolve_targets,
    )
    from app.api.v1.notifications_ws import get_user_contact
    _FEATURE_IMPORTS = True
except ImportError:
    _FEATURE_IMPORTS = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("notification_consumer")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REDIS_URL     = settings.REDIS_URL
RABBITMQ_URL  = settings.RABBITMQ_URL
REDIS_CHANNEL = "app_alerts"

# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------
_redis_client: Optional[sync_redis.Redis] = None

def _get_redis() -> sync_redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = sync_redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_keepalive=True,
            socket_keepalive_options={},
            socket_connect_timeout=5,
            socket_timeout=5,          # fail fast instead of waiting 60s
            retry_on_timeout=True,
            health_check_interval=30,  # ping Redis every 30s to keep connection alive
        )
    return _redis_client

# ---------------------------------------------------------------------------
# Sync DB helper — psycopg2 (safe to call from blocking pika thread)
# ---------------------------------------------------------------------------
_DB_URL: Optional[str] = None

def _get_sync_db_url() -> str:
    global _DB_URL
    if _DB_URL is None:
        url = settings.DATABASE_URL
        url = url.replace("postgresql+asyncpg://", "postgresql://")
        _DB_URL = url
    return _DB_URL


def _fetch_all_emails() -> List[Dict[str, str]]:
    """
    Query users and emergency_teams tables for all active emails.
    Uses psycopg2 (sync) — safe to call from blocking pika thread.
    """
    results: List[Dict[str, str]] = []
    try:
        conn = psycopg2.connect(_get_sync_db_url())
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT email, full_name
            FROM users
            WHERE status = 'ACTIVE'
              AND email IS NOT NULL AND email != ''
              AND deleted_at IS NULL
        """)
        for row in cur.fetchall():
            results.append({
                "email":     row["email"],
                "full_name": row["full_name"] or "Citizen",
                "user_type": "citizen",
            })

        cur.execute("""
            SELECT email, full_name
            FROM emergency_teams
            WHERE status = 'ACTIVE'
              AND email IS NOT NULL AND email != ''
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
        logger.info(f"_fetch_all_emails: {len(results)} addresses found")
    except Exception as exc:
        logger.error(f"_fetch_all_emails failed: {exc}")
    return results


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SEVERITY_COLOUR = {
    "CRITICAL": "red", "HIGH": "orange", "MEDIUM": "yellow",
    "LOW": "blue",     "INFO": "green",
}

RADIUS_KM = {
    "CRITICAL": 3.0, "HIGH": 2.0, "MEDIUM": 1.0,
    "LOW": 0.5,      "INFO": 0.5,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latlon(d: dict):
    """
    Extract (lat, lon) from a data dict.
    Handles multiple formats publishers use:
      - {"location": {"lat": x, "lon": y}}   (deployment, disaster services)
      - {"lat": x, "lon": y}                 (evaluation, reroute services)
      - {"disaster_lat": x, "disaster_lon": y} (evacuation service)
    Returns (None, None) if no coordinates found.
    """
    # Format 1: nested location dict
    loc = d.get("location")
    if isinstance(loc, dict) and loc.get("lat") is not None:
        return loc.get("lat"), loc.get("lon")
    # Format 2: top-level lat/lon
    if d.get("lat") is not None:
        return d.get("lat"), d.get("lon")
    # Format 3: disaster_lat / disaster_lon (evacuation service)
    if d.get("disaster_lat") is not None:
        return d.get("disaster_lat"), d.get("disaster_lon")
    return None, None


def _publish(alert: dict) -> None:
    global _redis_client
    try:
        _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
        logger.info(f"[Redis → {REDIS_CHANNEL}] {alert.get('event_type')}")
    except Exception as exc:
        logger.warning(f"Redis publish failed, reconnecting: {exc}")
        _redis_client = None  # force reconnect on next call
        try:
            _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
            logger.info(f"[Redis → {REDIS_CHANNEL}] {alert.get('event_type')} (after reconnect)")
        except Exception as exc2:
            logger.error(f"Redis publish failed after reconnect: {exc2}")


def _deliver(
    service: str,
    event_type: str,
    severity: str,
    title: str,
    message: str,
    data: dict,
    disaster_id: str = None,
    disaster_lat: float = None,
    disaster_lon: float = None,
    radius_km: float = None,
    broadcast: bool = False,
    location: str = "",
    direct_notify_ids: Optional[Set[str]] = None,
    target_roles: Optional[Set[str]] = None,
) -> None:
    """
    Unified delivery:
    1. Resolve geo targets
    2. Publish to Redis (WebSocket) — includes target_roles for role-based routing
    3. Queue for offline users
    4. Email HIGH/CRITICAL to all active users
    5. SMS CRITICAL only to users in impact zone

    target_roles: if set, these user types receive the notification regardless
                  of geo-targeting (e.g. {"emergency_team"} for ERT-wide alerts,
                  {"admin"} is always added automatically in broadcast_to_users).
    """
    tracking_id = data.get("tracking_id", "")

    # ── Step 1: Resolve targets ───────────────────────────────────────────
    if broadcast or not disaster_id:
        target_ids: Optional[Set[str]] = None
    elif _FEATURE_IMPORTS and disaster_lat is not None and disaster_lon is not None:
        r = radius_km or RADIUS_KM.get(severity.upper(), 1.0)
        target_ids = resolve_targets(disaster_id, disaster_lat, disaster_lon, r)
    elif _FEATURE_IMPORTS:
        target_ids = get_disaster_subscribers(disaster_id)
    else:
        target_ids = None

    if direct_notify_ids:
        target_ids = (target_ids or set()) | direct_notify_ids

    # ── Step 2: Publish to Redis ──────────────────────────────────────────
    alert = {
        "service":         service,
        "event_type":      event_type,
        "severity":        severity,
        "colour":          SEVERITY_COLOUR.get(severity.upper(), "blue"),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "title":           title,
        "message":         message,
        "data":            data,
        "target_user_ids": list(target_ids) if target_ids is not None else None,
        "target_roles":    list(target_roles) if target_roles is not None else None,
    }
    _publish(alert)

    # ── Step 3: Queue for offline users ──────────────────────────────────
    if _FEATURE_IMPORTS and target_ids is not None:
        offline = {k: v for k, v in alert.items() if k != "target_user_ids"}
        for uid in target_ids:
            if not is_user_online(uid):
                queue_offline_alert(uid, offline)

    # ── Steps 4 & 5: External notifications ──────────────────────────────
    if not _FEATURE_IMPORTS:
        return
    if not should_send_external(severity):
        return  # LOW / INFO / MEDIUM → WebSocket only

    sms_body  = f"[DRS] {title}\n{message}\nRef: {tracking_id}"
    html_body = build_html(title, message, tracking_id, severity, location)
    subject   = f"[DRS Alert] {title}"
    sent_emails: Set[str] = set()

    for contact in _fetch_all_emails():
        addr = contact.get("email", "")
        if addr and addr not in sent_emails:
            send_email(addr, subject, sms_body, html_body)
            sent_emails.add(addr)
    logger.info(f"[{event_type}] Emails sent to {len(sent_emails)} addresses")

    if severity.upper() == "CRITICAL":
        sent_phones: Set[str] = set()
        for uid in (target_ids or set()):
            contact = get_user_contact(uid)
            phone   = contact.get("phone", "")
            if phone and phone not in sent_phones:
                send_sms(phone, sms_body)
                sent_phones.add(phone)
                logger.info(f"Evacuation SMS → {phone[:7]}*** (user near disaster)")
        logger.info(f"[{event_type}] CRITICAL: SMS sent to {len(sent_phones)} nearby users")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_disaster_evaluated(data: dict) -> None:
    severity = data.get("severity", "MEDIUM").upper()
    lat, lon = _latlon(data)
    disaster_type = (data.get("type") or "disaster").lower()
    _deliver(
        "disaster", "disaster.evaluated", severity,
        f"New disaster — {data.get('location_address', data.get('tracking_id', ''))}",
        f"A {disaster_type} has been reported. "
        f"Severity: {severity}. Emergency services have been notified.",
        {
            "disaster_id":          data.get("disaster_id"),
            "tracking_id":          data.get("tracking_id"),
            "type":                 data.get("type"),
            "severity":             severity,
            "confidence":           data.get("confidence"),
            "trigger_reroute":      data.get("trigger_reroute"),
            "trigger_deploy":       data.get("trigger_deploy"),
            "recommended_services": data.get("recommended_services", []),
            "affected_roads":       data.get("affected_roads", []),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        # Broadcast to ALL connected users — public safety alert.
        # Anyone using the disaster response app should know about a new disaster.
        broadcast=True,
    )


def _on_disaster_dispatched(data: dict) -> None:
    units = data.get("units_dispatched", 0)
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.dispatched", "HIGH",
        f"Units dispatched — {data.get('tracking_id', '')}",
        f"{units} unit(s) dispatched with {data.get('priority_level', 'STANDARD')} priority.",
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "units_dispatched": units,
            "priority_level":   data.get("priority_level"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        # ERT need to know their units are moving; admin monitors deployments
        target_roles={"emergency_team"},
    )


def _on_disaster_verified(data: dict) -> None:
    lat, lon = _latlon(data)
    reporter_id = data.get("reporter_id")
    _deliver(
        "disaster", "disaster.verified", "HIGH",
        f"Emergency team on scene — {data.get('tracking_id', '')}",
        f"Emergency services have arrived. Situation: {data.get('situation_report', 'Under control')}",
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "verified_by_unit": data.get("verified_by_unit"),
            "situation_report": data.get("situation_report"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        direct_notify_ids={reporter_id} if reporter_id else None,
        target_roles={"emergency_team"},
        broadcast=True
    )


def _on_disaster_updated(data: dict) -> None:
    update_type = data.get("update_type", "")

    # Skip internal system updates — not useful to any user
    # reporter_notification = evaluation service acknowledging the report
    SKIP_UPDATE_TYPES = {"reporter_notification", "report_received"}
    if update_type in SKIP_UPDATE_TYPES:
        logger.info(f"[disaster.updated] skipping internal update_type={update_type}")
        return

    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.updated", "LOW",
        f"Disaster update — {data.get('tracking_id', '')}",
        data.get("details", "The disaster status has been updated."),
        {
            "disaster_id": data.get("disaster_id"),
            "tracking_id": data.get("tracking_id"),
            "update_type": update_type,
            "details":     data.get("details"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        target_roles={"emergency_team"},
    )


def _on_disaster_resolved(data: dict) -> None:
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.resolved", "INFO",
        f"Disaster resolved — {data.get('tracking_id', '')}",
        data.get("resolution_notes", "The situation has been resolved. Normal conditions resuming."),
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "resolution_notes": data.get("resolution_notes"),
            "resolved_time":    data.get("resolved_time"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        target_roles={"emergency_team"},
        broadcast=True
    )


def _on_backup_requested(data: dict) -> None:
    lat, lon = _latlon(data)
    resources = data.get("resources_needed") or ["Unspecified"]
    direct = set(filter(None, [
        data.get("requesting_unit_user_id"),
        data.get("coordinator_id"),
    ])) or None
    _deliver(
        "disaster", "disaster.backup_requested", "CRITICAL",
        f"URGENT: Backup needed — {data.get('tracking_id', '')}",
        f"Field unit needs backup. Resources: {', '.join(resources)}",
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "requesting_unit":  data.get("requesting_unit"),
            "resources_needed": resources,
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        direct_notify_ids=direct,
        target_roles={"emergency_team"},  # ERT-wide + admin
    )


def _on_unit_completed(data: dict) -> None:
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.unit_completed", "INFO",
        f"Unit mission complete — {data.get('tracking_id', '')}",
        "A response unit has completed its mission.",
        {
            "disaster_id": data.get("disaster_id"),
            "tracking_id": data.get("tracking_id"),
            "unit_id":     data.get("unit_id"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        target_roles={"emergency_team"},
    )


def _on_reroute_triggered(data: dict) -> None:
    vehicles = data.get("vehicles_count") or len(data.get("vehicles", []))
    lat, lon = _latlon(data)

    # direct_notify_ids = the exact vehicle user IDs computed by the reroute service.
    # These citizens get their personal route_assignments in the payload.
    # ERT/admin get it via target_roles regardless of location.
    route_assignments = data.get("route_assignments", {})
    affected_vehicle_ids = set(route_assignments.keys()) if route_assignments else None

    _deliver(
        "reroute", "reroute.triggered", "HIGH",
        "Traffic reroute activated",
        f"{vehicles} vehicle(s) rerouted. Check your map for the updated route.",
        {
            "disaster_id":      data.get("disaster_id"),
            "plan_id":          data.get("plan_id"),
            "vehicles_count":   vehicles,
            "vehicles":         data.get("vehicles", []),
            "route_assignments":route_assignments,
            "overflow_count":   data.get("overflow_count", 0),
            "routes":           data.get("routes", []),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
        direct_notify_ids=affected_vehicle_ids,
        target_roles={"emergency_team"},  # ERT/admin always gets reroute notifications
    )


def _on_route_updated(data: dict) -> None:
    reason = data.get("reason", "congestion")
    lat, lon = _latlon(data)

    reason_titles = {
        "operator_override": "Emergency corridor reserved",
        "lane_closure":      "Lane closed ahead",
        "congestion":        "Heavy congestion detected",
        "recalculation":     "Route recalculated",
    }
    reason_messages = {
        "operator_override": "An emergency corridor has been reserved. Your route has been updated — please follow the new directions.",
        "lane_closure":      "A lane closure is ahead. Alternative route assigned.",
        "congestion":        "Heavy congestion on your route. Alternative route calculated.",
        "recalculation":     "Your route has been updated due to changing conditions.",
    }
    title   = reason_titles.get(reason, f"Route updated — {reason}")
    msg     = reason_messages.get(reason, f"Traffic routes updated due to {reason}.")

    _deliver(
        "reroute", "route.updated", "MEDIUM",
        title, msg,
        {
            "disaster_id":      data.get("disaster_id"),
            "reason":           reason,
            "route_assignments":data.get("route_assignments", {}),
            "routes":           data.get("routes", []),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
        target_roles={"emergency_team"},
    )


def _on_disaster_cleared(data: dict) -> None:
    lat, lon = _latlon(data)

    # Extract the vehicle user IDs that were rerouted — same users
    # who got reroute.triggered need to know they can resume normal flow.
    users = data.get("users", [])
    affected_vehicle_ids = (
        {u.get("user_id") for u in users if u.get("user_id")}
        if isinstance(users, list) and users
        else None
    )

    _deliver(
        "reroute", "disaster.cleared", "INFO",
        "Roads cleared — normal flow restored",
        f"The incident has been cleared. {data.get('cleared_segments', 0)} road segment(s) "
        "reopened. You can resume your normal route.",
        {
            "disaster_id":      data.get("disaster_id"),
            "cleared_segments": data.get("cleared_segments"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
        direct_notify_ids=affected_vehicle_ids,
        target_roles={"emergency_team"},
    )


def _on_road_impact(data: dict) -> None:
    lat, lon = _latlon(data)
    dtype = data.get("type", "disaster").lower()
    _deliver(
        "reroute", "reroute.road_impact_alert", "HIGH",
        f"Road impact alert — {data.get('tracking_id', '')}",
        f"A {dtype} confirmed nearby. Expect road disruptions. Check live map for routes.",
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "disaster_type":    data.get("type"),
            "severity":         data.get("severity"),
            "location_address": data.get("location_address"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
        location=data.get("location_address", ""),
        target_roles={"emergency_team"},
    )


def _on_team_assigned(data: dict) -> None:
    lat, lon = _latlon(data)
    dept = data.get("assigned_department", "Emergency services")
    _deliver(
        "coordination", "coordination.team_assigned", "HIGH",
        f"Response team assigned — {data.get('tracking_id', '')}",
        f"{dept} assigned to respond. Help is on the way.",
        {
            "disaster_id":         data.get("disaster_id"),
            "tracking_id":         data.get("tracking_id"),
            "assigned_department": dept,
            "verified_by_unit":    data.get("verified_by_unit"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=1.0,
        location=data.get("location_address", ""),
        target_roles={"emergency_team"},
    )


def _on_escalation(data: dict) -> None:
    lat, lon = _latlon(data)
    resources = data.get("resources_needed") or ["Unspecified"]
    _deliver(
        "coordination", "coordination.escalation", "CRITICAL",
        f"URGENT: Escalation — {data.get('tracking_id', '')}",
        f"Field unit needs resources: {', '.join(resources)}. Coordination action required.",
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "requesting_unit":  data.get("requesting_unit"),
            "resources_needed": resources,
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=1.0,
        location=data.get("location_address", ""),
        target_roles={"emergency_team"},
    )


def _on_disaster_reported(data: dict) -> None:
    """evaluation_queue — log only, no user notification (disaster not on map yet)."""
    logger.info(
        f"[evaluation_queue] disaster.reported — "
        f"id={data.get('disaster_id')} type={data.get('type')}"
    )


def _on_false_alarm(data: dict) -> None:
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.false_alarm", "INFO",
        f"False alarm — {data.get('tracking_id', '')}",
        "Field unit confirmed no emergency at this location. Report has been closed.",
        {
            "disaster_id":      data.get("disaster_id"),
            "tracking_id":      data.get("tracking_id"),
            "flagged_by_unit":  data.get("flagged_by_unit"),
            "situation_report": data.get("situation_report"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        target_roles={"emergency_team"},
        broadcast=True
    )


def _on_evacuation_triggered(data: dict) -> None:
    """
    Published by evacuation_service when a plan is activated.
    CRITICAL — targets users physically inside the evacuation zones.
    Each user's assigned evacuation route is in data["users"][user_id].
    """
    lat   = data.get("lat") or data.get("disaster_lat")
    lon   = data.get("lon") or data.get("disaster_lon")
    total = data.get("total_users", 0)
    _deliver(
        "evacuation", "evacuation.triggered", "CRITICAL",
        "EVACUATION ORDER — leave the area now",
        f"An evacuation has been ordered for your area. "
        f"{total} people are being evacuated. Follow the route on your map immediately.",
        {
            "disaster_id": data.get("disaster_id"),
            "plan_id":     data.get("plan_id"),
            "total_users": total,
            "users":       data.get("users", {}),
            "timestamp":   data.get("timestamp"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=5.0,
        location=data.get("location_address", ""),
        # ERT must also know evacuation is in progress
        target_roles={"emergency_team"},
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Per-queue handler tables
# ---------------------------------------------------------------------------
# Each queue only triggers the handlers relevant to its purpose.
# This prevents duplicate notifications when the same event is bound
# to multiple queues (e.g. disaster.evaluated → notification_queue AND reroute_queue).

# notification_queue — all user-facing notifications
NOTIFICATION_HANDLERS = {
    "disaster.evaluated":        _on_disaster_evaluated,
    "disaster.dispatched":       _on_disaster_dispatched,
    "disaster.verified":         _on_disaster_verified,
    "disaster.updated":          _on_disaster_updated,
    "disaster.resolved":         _on_disaster_resolved,
    "disaster.backup_requested": _on_backup_requested,
    "disaster.unit_completed":   _on_unit_completed,
    "disaster.false_alarm":      _on_false_alarm,
    "disaster.reported":         _on_disaster_reported,
}

# notification.reroute — reroute events from reroute_publisher (reroute.events exchange)
REROUTE_NOTIFICATION_HANDLERS = {
    "reroute.triggered":         _on_reroute_triggered,
    "route.updated":             _on_route_updated,
    "disaster.cleared":          _on_disaster_cleared,
    "reroute.road_impact_alert": _on_road_impact,
}

# reroute_queue — disaster events that trigger the reroute service pipeline
# These are consumed here for LOGGING only — actual reroute triggering is
# done by the downstream reroute service. No notifications sent from here.
REROUTE_TRIGGER_HANDLERS = {
    "disaster.evaluated":  _on_disaster_reported,  # log only
    "disaster.verified":   _on_disaster_reported,  # log only
    "disaster.resolved":   _on_disaster_reported,  # log only
    "disaster.unit_completed": _on_disaster_reported,  # log only
}

# coordination_queue — team assignment and escalation
COORDINATION_HANDLERS = {
    "disaster.verified":         _on_team_assigned,
    "disaster.backup_requested": _on_escalation,
}

# evaluation_queue — new disaster reports to be evaluated (log only here)
EVALUATION_HANDLERS = {
    "disaster.reported": _on_disaster_reported,
}

# evacuation_queue — evacuation plan activated
EVACUATION_HANDLERS = {
    "evacuation.triggered": _on_evacuation_triggered,
}

# Map queue name → its handler table
QUEUE_HANDLERS = {
    "notification_queue":   NOTIFICATION_HANDLERS,
    "notification.reroute": REROUTE_NOTIFICATION_HANDLERS,
    "reroute_queue":        REROUTE_TRIGGER_HANDLERS,
    "coordination_queue":   COORDINATION_HANDLERS,
    "evaluation_queue":     EVALUATION_HANDLERS,
    "evacuation_queue":     EVACUATION_HANDLERS,
}

ALL_QUEUES = [
    "notification_queue",
    "notification.reroute",
    "reroute_queue",
    "evaluation_queue",
    "coordination_queue",
    "evacuation_queue",
]

# ---------------------------------------------------------------------------
# Unified Consumer
# ---------------------------------------------------------------------------

class UnifiedConsumer:
    """One process, one connection, consuming all queues."""

    def __init__(self):
        self.rabbitmq_url = RABBITMQ_URL
        self.connection   = None
        self.channel      = None
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        logger.info("Shutting down unified consumer...")
        if self.connection and not self.connection.is_closed:
            self.connection.close()
        sys.exit(0)

    def connect(self) -> bool:
        for attempt in range(1, 6):
            try:
                params = pika.URLParameters(self.rabbitmq_url)
                self.connection = pika.BlockingConnection(params)
                self.channel    = self.connection.channel()
                self.channel.basic_qos(prefetch_count=1)
                logger.info(f"Connected to RabbitMQ (attempt {attempt})")
                return True
            except Exception as e:
                logger.error(f"Connection failed (attempt {attempt}/5): {e}")
                if attempt < 5:
                    time.sleep(3)
        return False

    def _make_callback(self, queue_name: str):
        # Get the handler table for this specific queue
        handlers = QUEUE_HANDLERS.get(queue_name, {})

        def callback(ch, method, properties, body):
            try:
                data       = json.loads(body)
                event_type = data.get("event_type") or data.get("event", "unknown")
                data["event_type"] = event_type
                logger.info(f"[{queue_name}] {event_type}")
                handler = handlers.get(event_type)
                if handler:
                    handler(data)
                else:
                    logger.warning(f"[{queue_name}] No handler for '{event_type}' — skipping")
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                logger.error(f"[{queue_name}] Error: {e}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return callback

    def start(self):
        if not self.connect():
            logger.error("Could not connect to RabbitMQ — exiting")
            return

        for queue in ALL_QUEUES:
            try:
                self.channel.queue_declare(queue=queue, durable=True, passive=True)
            except Exception:
                try:
                    self.channel = self.connection.channel()
                    self.channel.basic_qos(prefetch_count=1)
                    self.channel.queue_declare(queue=queue, durable=True)
                except Exception as e:
                    logger.warning(f"Could not declare {queue}: {e}")
                    continue

            self.channel.basic_consume(
                queue=queue,
                on_message_callback=self._make_callback(queue),
                auto_ack=False,
            )
            logger.info(f"✅ Subscribed to: {queue}")

        print(f"\n{'='*60}")
        print(f"  UNIFIED CONSUMER")
        print(f"  Queues: {', '.join(ALL_QUEUES)}")
        print(f"  Redis → {REDIS_CHANNEL}")
        print(f"  SMS/Email: {'enabled' if _FEATURE_IMPORTS else 'disabled (imports not found)'}")
        print(f"  Press Ctrl+C to stop")
        print(f"{'='*60}\n")

        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            self._shutdown(None, None)


if __name__ == "__main__":
    consumer = UnifiedConsumer()
    consumer.start()