# File: app/consumers/notification_consumer.py
"""
Notification Consumer.

Listens on notification_queue which is already bound (in rabbitmq_service.py)
to ALL notification-relevant routing keys:

    Routing key               Published by
    ──────────────────────    ────────────────────────────────────────────
    disaster.dispatched       deployment_service  → units sent to disaster
    disaster.verified         deployment_service  → field unit on-scene confirmed
    disaster.updated          disaster_service    → status / response recorded
    disaster.resolved         disaster_service    → disaster fully resolved
    disaster.backup_requested deployment_service  → field unit needs more resources
    disaster.unit_completed   deployment_service  → a unit finished its mission

For every message this consumer:
  1. Reads the event_type field injected by rabbitmq_service.publish()
  2. Calls the matching handler to build a standardised alert envelope
  3. Publishes the envelope to Redis pub/sub channel 'app_alerts'

The async redis_listener() in notifications_ws.py is subscribed to
'app_alerts' and fans every alert out to all connected WebSocket clients.

Run standalone:
    python -m app.consumers.notification_consumer
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from dotenv import load_dotenv
load_dotenv()

import redis as sync_redis
from app.consumers.base_consumer import BaseConsumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("notification_consumer")

REDIS_URL     = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHANNEL = "app_alerts"

_redis_client: Optional[sync_redis.Redis] = None


def _get_redis() -> sync_redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = sync_redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


SEVERITY_COLOUR = {
    "CRITICAL": "red",
    "HIGH":     "orange",
    "MEDIUM":   "yellow",
    "LOW":      "blue",
    "INFO":     "green",
}


def _publish(alert: dict) -> None:
    """Publish a formatted alert envelope to Redis pub/sub."""
    try:
        _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
        logger.info(f"[Redis → {REDIS_CHANNEL}] {alert['event_type']}")
    except Exception as exc:
        logger.error(f"Redis publish failed: {exc}")


# ══════════════════════════════════════════════════════════════
class NotificationConsumer(BaseConsumer):
    """
    Single consumer on notification_queue.

    Handles all six routing keys the system publishes to this queue.
    Each handler builds the standard alert envelope and pushes it
    to Redis so the WebSocket layer can broadcast it to the frontend.
    """

    def __init__(self):
        # notification_queue is already declared + bound in rabbitmq_service.py
        super().__init__(queue_name="notification_queue")

    # ── main dispatch ─────────────────────────────────────────

    def process_message(self, data: dict) -> None:
        event_type = data.get("event_type", "")
        handlers = {
            "disaster.dispatched":       self._on_dispatched,
            "disaster.verified":         self._on_verified,
            "disaster.updated":          self._on_updated,
            "disaster.resolved":         self._on_resolved,
            "disaster.backup_requested": self._on_backup_requested,
            "disaster.unit_completed":   self._on_unit_completed,
        }
        handler = handlers.get(event_type)
        if handler:
            handler(data)
        else:
            logger.warning(f"Unrecognised event_type '{event_type}' — skipping")

    # ── per-event handlers ────────────────────────────────────

    def _on_dispatched(self, data: dict) -> None:
        """Units sent to a disaster. Published by deployment_service.dispatch_units()"""
        units    = data.get("units_dispatched", 0)
        priority = data.get("priority_level", "STANDARD")
        _publish({
            "service":    "disaster",
            "event_type": "disaster.dispatched",
            "severity":   "HIGH",
            "colour":     SEVERITY_COLOUR["HIGH"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "title":      f"Units dispatched — {data.get('tracking_id', '')}",
            "message":    f"{units} unit(s) dispatched with {priority} priority.",
            "data": {
                "disaster_id":      data.get("disaster_id"),
                "tracking_id":      data.get("tracking_id"),
                "units_dispatched": units,
                "priority_level":   priority,
            },
        })

    def _on_verified(self, data: dict) -> None:
        """Field unit arrived on-scene and confirmed the disaster (ON_SCENE transition)."""
        _publish({
            "service":    "disaster",
            "event_type": "disaster.verified",
            "severity":   "HIGH",
            "colour":     SEVERITY_COLOUR["HIGH"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "title":      f"Disaster confirmed on-scene — {data.get('tracking_id', '')}",
            "message": (
                f"Emergency unit has arrived and confirmed the disaster. "
                f"Report: {data.get('situation_report', 'N/A')}"
            ),
            "data": {
                "disaster_id":      data.get("disaster_id"),
                "tracking_id":      data.get("tracking_id"),
                "verified_by_unit": data.get("verified_by_unit"),
                "situation_report": data.get("situation_report"),
            },
        })

    def _on_updated(self, data: dict) -> None:
        """Generic status / response-time update. Published by disaster_service."""
        _publish({
            "service":    "disaster",
            "event_type": "disaster.updated",
            "severity":   "LOW",
            "colour":     SEVERITY_COLOUR["LOW"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "title":      f"Disaster update — {data.get('tracking_id', '')}",
            "message":    data.get("details", "The disaster status has been updated."),
            "data": {
                "disaster_id": data.get("disaster_id"),
                "tracking_id": data.get("tracking_id"),
                "update_type": data.get("update_type"),
                "details":     data.get("details"),
            },
        })

    def _on_resolved(self, data: dict) -> None:
        """Disaster fully resolved. Published by disaster_service.resolve_disaster()."""
        _publish({
            "service":    "disaster",
            "event_type": "disaster.resolved",
            "severity":   "INFO",
            "colour":     SEVERITY_COLOUR["INFO"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "title":      f"Disaster resolved — {data.get('tracking_id', '')}",
            "message":    data.get("resolution_notes", "The situation has been resolved."),
            "data": {
                "disaster_id":      data.get("disaster_id"),
                "tracking_id":      data.get("tracking_id"),
                "resolution_notes": data.get("resolution_notes"),
                "resolved_time":    data.get("resolved_time"),
            },
        })

    def _on_backup_requested(self, data: dict) -> None:
        """Field unit needs more resources. Published when request_immediate_backup=True."""
        resources = data.get("resources_needed") or ["Unspecified"]
        _publish({
            "service":    "disaster",
            "event_type": "disaster.backup_requested",
            "severity":   "CRITICAL",
            "colour":     SEVERITY_COLOUR["CRITICAL"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "title":      f"Backup requested — {data.get('tracking_id', '')}",
            "message": (
                f"Field unit is requesting immediate backup. "
                f"Resources needed: {', '.join(resources)}"
            ),
            "data": {
                "disaster_id":      data.get("disaster_id"),
                "tracking_id":      data.get("tracking_id"),
                "requesting_unit":  data.get("requesting_unit"),
                "resources_needed": resources,
            },
        })

    def _on_unit_completed(self, data: dict) -> None:
        """A unit has completed its deployment. Published on COMPLETED status transition."""
        _publish({
            "service":    "disaster",
            "event_type": "disaster.unit_completed",
            "severity":   "INFO",
            "colour":     SEVERITY_COLOUR["INFO"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "title":      f"Unit mission complete — {data.get('tracking_id', '')}",
            "message":    "A response unit has completed its mission at this incident.",
            "data": {
                "disaster_id": data.get("disaster_id"),
                "tracking_id": data.get("tracking_id"),
                "unit_id":     data.get("unit_id"),
            },
        })


if __name__ == "__main__":
    consumer = NotificationConsumer()
    consumer.start()