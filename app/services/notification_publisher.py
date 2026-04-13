# File: app/services/notification_publisher.py
"""
Notification Publisher — shared utility for ALL services.

Any service that wants to push a real-time frontend alert without
going through RabbitMQ (e.g. a direct API action) can call these
functions.  They publish the standard alert envelope directly to
Redis pub/sub channel 'app_alerts'.

Two variants:
  publish_alert()        → sync  — safe in pika consumer threads, scripts
  publish_alert_async()  → async — safe inside FastAPI endpoint handlers

Standard alert envelope published to Redis:
{
    "service":    "<service name>",
    "event_type": "<event.type>",
    "severity":   "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "INFO",
    "colour":     "green" | "blue" | "yellow" | "orange" | "red",
    "title":      "<short headline>",
    "message":    "<human-readable description>",
    "data":       { ... },
    "timestamp":  "<ISO-8601>"
}

Example (from any service, sync):
    from app.services.notification_publisher import publish_alert
    publish_alert(
        service="coordination",
        event_type="team.deployed",
        severity="HIGH",
        title="Team dispatched",
        message="Medical unit en route to Grafton Street.",
        data={"unit_id": "...", "eta_minutes": 8},
    )
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis
import redis.asyncio as aioredis

logger = logging.getLogger("notification_publisher")

REDIS_URL     = os.getenv("REDIS_URL", "redis://20.90.162.121:7001")
REDIS_CHANNEL = "app_alerts"

COLOUR_MAP = {
    "CRITICAL": "red",
    "HIGH":     "orange",
    "MEDIUM":   "yellow",
    "LOW":      "blue",
    "INFO":     "green",
}

# ── sync singleton ────────────────────────────────────────────
_sync_client: Optional[redis.Redis] = None


def _get_sync() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _sync_client


def _build_envelope(
    service: str,
    event_type: str,
    severity: str,
    title: str,
    message: str,
    data: Optional[Dict[str, Any]],
) -> str:
    envelope = {
        "service":    service,
        "event_type": event_type,
        "severity":   severity.upper(),
        "colour":     COLOUR_MAP.get(severity.upper(), "blue"),
        "title":      title,
        "message":    message,
        "data":       data or {},
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(envelope, default=str)


# ══════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════

def publish_alert(
    service: str,
    event_type: str,
    title: str,
    message: str,
    severity: str = "LOW",
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Publish from a SYNC context (consumer threads, scripts).
    Returns True on success, False on failure. Never raises.
    """
    try:
        _get_sync().publish(REDIS_CHANNEL, _build_envelope(
            service, event_type, severity, title, message, data
        ))
        logger.info(f"Alert published [{service}/{event_type}]")
        return True
    except Exception as exc:
        logger.error(f"Alert publish failed [{service}/{event_type}]: {exc}")
        return False


async def publish_alert_async(
    service: str,
    event_type: str,
    title: str,
    message: str,
    severity: str = "LOW",
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Publish from an ASYNC context (FastAPI endpoint handlers).
    Returns True on success, False on failure. Never raises.
    """
    try:
        client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await client.publish(REDIS_CHANNEL, _build_envelope(
            service, event_type, severity, title, message, data
        ))
        await client.aclose()
        logger.info(f"Alert published (async) [{service}/{event_type}]")
        return True
    except Exception as exc:
        logger.error(f"Alert publish failed (async) [{service}/{event_type}]: {exc}")
        return False