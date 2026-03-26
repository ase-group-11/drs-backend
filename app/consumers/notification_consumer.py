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
        _redis_client = sync_redis.from_url(REDIS_URL, decode_responses=True)
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
    loc = d.get("location") or {}
    return (loc.get("lat"), loc.get("lon")) if isinstance(loc, dict) else (None, None)


def _publish(alert: dict) -> None:
    """Publish alert envelope to Redis → WebSocket broadcaster."""
    try:
        _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
        logger.info(f"[Redis → {REDIS_CHANNEL}] {alert.get('event_type')}")
    except Exception as exc:
        logger.error(f"Redis publish failed: {exc}")


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
) -> None:
    """
    Unified delivery:
    1. Resolve geo targets
    2. Publish to Redis (WebSocket)
    3. Queue for offline users
    4. Email HIGH/CRITICAL to all active users
    5. SMS CRITICAL only to users in impact zone
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
    _deliver(
        "disaster", "disaster.evaluated", severity,
        f"Disaster evaluated — {data.get('tracking_id', '')}",
        f"Severity: {severity}. Confidence: {data.get('confidence', 0):.0%}. "
        f"Trigger reroute: {data.get('trigger_reroute', False)}.",
        {
            "disaster_id":         data.get("disaster_id"),
            "tracking_id":         data.get("tracking_id"),
            "severity":            severity,
            "confidence":          data.get("confidence"),
            "trigger_reroute":     data.get("trigger_reroute"),
            "trigger_deploy":      data.get("trigger_deploy"),
            "recommended_services":data.get("recommended_services", []),
            "affected_roads":      data.get("affected_roads", []),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
    )


def _on_disaster_dispatched(data: dict) -> None:
    units = data.get("units_dispatched", 0)
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.dispatched", "HIGH",
        f"Units dispatched — {data.get('tracking_id', '')}",
        f"{units} unit(s) dispatched with {data.get('priority_level', 'STANDARD')} priority.",
        {
            "disaster_id":    data.get("disaster_id"),
            "tracking_id":    data.get("tracking_id"),
            "units_dispatched": units,
            "priority_level": data.get("priority_level"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
    )


def _on_disaster_verified(data: dict) -> None:
    lat, lon = _latlon(data)
    reporter_id = data.get("reporter_id")
    _deliver(
        "disaster", "disaster.verified", "HIGH",
        f"Disaster confirmed on-scene — {data.get('tracking_id', '')}",
        f"Emergency unit confirmed. Report: {data.get('situation_report', 'N/A')}",
        {
            "disaster_id":       data.get("disaster_id"),
            "tracking_id":       data.get("tracking_id"),
            "verified_by_unit":  data.get("verified_by_unit"),
            "situation_report":  data.get("situation_report"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        direct_notify_ids={reporter_id} if reporter_id else None,
    )


def _on_disaster_updated(data: dict) -> None:
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.updated", "LOW",
        f"Disaster update — {data.get('tracking_id', '')}",
        data.get("details", "The disaster status has been updated."),
        {
            "disaster_id": data.get("disaster_id"),
            "tracking_id": data.get("tracking_id"),
            "update_type": data.get("update_type"),
            "details":     data.get("details"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
    )


def _on_disaster_resolved(data: dict) -> None:
    lat, lon = _latlon(data)
    _deliver(
        "disaster", "disaster.resolved", "INFO",
        f"Disaster resolved — {data.get('tracking_id', '')}",
        data.get("resolution_notes", "The situation has been resolved."),
        {
            "disaster_id":       data.get("disaster_id"),
            "tracking_id":       data.get("tracking_id"),
            "resolution_notes":  data.get("resolution_notes"),
            "resolved_time":     data.get("resolved_time"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
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
            "disaster_id":       data.get("disaster_id"),
            "tracking_id":       data.get("tracking_id"),
            "requesting_unit":   data.get("requesting_unit"),
            "resources_needed":  resources,
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        location=data.get("location_address", ""),
        direct_notify_ids=direct,
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
    )


def _on_reroute_triggered(data: dict) -> None:
    vehicles = data.get("vehicles_count") or len(data.get("vehicles", []))
    lat, lon = _latlon(data)
    _deliver(
        "reroute", "reroute.triggered", "HIGH",
        "Traffic reroute activated",
        f"{vehicles} vehicles rerouted across {len(data.get('routes', []))} routes. "
        f"Overflow: {data.get('overflow_count', 0)}.",
        {
            "disaster_id":      data.get("disaster_id"),
            "plan_id":          data.get("plan_id"),
            "vehicles_count":   vehicles,
            "vehicles":         data.get("vehicles", []),           # ← citizen routing
            "route_assignments":data.get("route_assignments", {}),  # ← citizen routing
            "overflow_count":   data.get("overflow_count", 0),
            "routes":           data.get("routes", []),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
    )


def _on_route_updated(data: dict) -> None:
    reason = data.get("reason", "congestion")
    lat, lon = _latlon(data)
    _deliver(
        "reroute", "route.updated", "MEDIUM",
        f"Routes recalculated — {reason}",
        f"Traffic routes updated due to {reason}.",
        {
            "disaster_id": data.get("disaster_id"),
            "reason":      reason,
            "routes":      data.get("routes", []),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
    )


def _on_disaster_cleared(data: dict) -> None:
    lat, lon = _latlon(data)
    _deliver(
        "reroute", "disaster.cleared", "INFO",
        "Roads cleared — normal flow restored",
        f"Disaster cleared. {data.get('cleared_segments', 0)} road segment(s) reopened. "
        "Normal traffic flow has resumed.",
        {
            "disaster_id":     data.get("disaster_id"),
            "cleared_segments":data.get("cleared_segments"),
        },
        disaster_id=data.get("disaster_id"),
        disaster_lat=lat, disaster_lon=lon,
        radius_km=3.0,
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
    )


def _on_disaster_reported(data: dict) -> None:
    """evaluation_queue — log only for now."""
    logger.info(
        f"[evaluation_queue] disaster.reported — "
        f"id={data.get('disaster_id')} type={data.get('type')}"
    )


# ---------------------------------------------------------------------------
# Master dispatch table
# ---------------------------------------------------------------------------
HANDLERS = {
    "disaster.evaluated":           _on_disaster_evaluated,
    "disaster.dispatched":          _on_disaster_dispatched,
    "disaster.verified":            _on_disaster_verified,
    "disaster.updated":             _on_disaster_updated,
    "disaster.resolved":            _on_disaster_resolved,
    "disaster.backup_requested":    _on_backup_requested,
    "disaster.unit_completed":      _on_unit_completed,
    "reroute.triggered":            _on_reroute_triggered,
    "route.updated":                _on_route_updated,
    "disaster.cleared":             _on_disaster_cleared,
    "reroute.road_impact_alert":    _on_road_impact,
    "coordination.team_assigned":   _on_team_assigned,
    "coordination.escalation":      _on_escalation,
    "disaster.reported":            _on_disaster_reported,
}

ALL_QUEUES = [
    "notification_queue",
    "notification.reroute",
    "reroute_queue",
    "evaluation_queue",
    "coordination_queue",
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
        def callback(ch, method, properties, body):
            try:
                data       = json.loads(body)
                event_type = data.get("event_type") or data.get("event", "unknown")
                data["event_type"] = event_type
                logger.info(f"[{queue_name}] {event_type}")
                handler = HANDLERS.get(event_type)
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