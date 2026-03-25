"""
app/consumers/notification_consumer.py

Unified Consumer — handles ALL queues in one process, one connection.

Queues consumed:
    notification_queue      — disaster lifecycle events
    notification.reroute    — reroute events
    reroute_queue           — disaster events that trigger rerouting
    evaluation_queue        — disaster.reported events

Run:
    python -m app.consumers.notification_consumer
"""

import os
import sys
import json
import logging
import pathlib
import signal
import time
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv, dotenv_values
load_dotenv()

import pika
import redis as sync_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("unified_consumer")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_env = dotenv_values(pathlib.Path(__file__).parents[2] / ".env")
REDIS_URL     = _env.get("REDIS_URL") or os.getenv("REDIS_URL", "redis://:6379")
RABBITMQ_URL  = _env.get("RABBITMQ_URL") or os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
REDIS_CHANNEL = "app_alerts"

_redis_client: Optional[sync_redis.Redis] = None

def _get_redis() -> sync_redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = sync_redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client

SEVERITY_COLOUR = {
    "CRITICAL": "red", "HIGH": "orange", "MEDIUM": "yellow",
    "LOW": "blue", "INFO": "green",
}

def _publish(alert: dict) -> None:
    try:
        _get_redis().publish(REDIS_CHANNEL, json.dumps(alert, default=str))
        logger.info(f"[Redis → {REDIS_CHANNEL}] {alert['event_type']}")
    except Exception as exc:
        logger.error(f"Redis publish failed: {exc}")

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_disaster_evaluated(data: dict) -> None:
    severity = data.get("severity", "MEDIUM").upper()
    _publish({
        "service": "disaster", "event_type": "disaster.evaluated",
        "severity": severity, "colour": SEVERITY_COLOUR.get(severity, "yellow"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Disaster evaluated — {data.get('tracking_id', '')}",
        "message": (
            f"Severity: {severity}. Confidence: {data.get('confidence', 0):.0%}. "
            f"Trigger reroute: {data.get('trigger_reroute', False)}."
        ),
        "data": {
            "disaster_id": data.get("disaster_id"),
            "tracking_id": data.get("tracking_id"),
            "severity": severity,
            "confidence": data.get("confidence"),
            "trigger_reroute": data.get("trigger_reroute"),
            "trigger_deploy": data.get("trigger_deploy"),
            "recommended_services": data.get("recommended_services", []),
            "affected_roads": data.get("affected_roads", []),
        },
    })

def _on_disaster_dispatched(data: dict) -> None:
    units = data.get("units_dispatched", 0)
    _publish({
        "service": "disaster", "event_type": "disaster.dispatched",
        "severity": "HIGH", "colour": SEVERITY_COLOUR["HIGH"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Units dispatched — {data.get('tracking_id', '')}",
        "message": f"{units} unit(s) dispatched with {data.get('priority_level', 'STANDARD')} priority.",
        "data": {"disaster_id": data.get("disaster_id"), "tracking_id": data.get("tracking_id"),
                 "units_dispatched": units},
    })

def _on_disaster_verified(data: dict) -> None:
    _publish({
        "service": "disaster", "event_type": "disaster.verified",
        "severity": "HIGH", "colour": SEVERITY_COLOUR["HIGH"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Disaster confirmed on-scene — {data.get('tracking_id', '')}",
        "message": f"Emergency unit confirmed. Report: {data.get('situation_report', 'N/A')}",
        "data": {"disaster_id": data.get("disaster_id"), "tracking_id": data.get("tracking_id"),
                 "verified_by_unit": data.get("verified_by_unit")},
    })

def _on_disaster_updated(data: dict) -> None:
    _publish({
        "service": "disaster", "event_type": "disaster.updated",
        "severity": "LOW", "colour": SEVERITY_COLOUR["LOW"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Disaster update — {data.get('tracking_id', '')}",
        "message": data.get("details", "The disaster status has been updated."),
        "data": {"disaster_id": data.get("disaster_id"), "tracking_id": data.get("tracking_id"),
                 "update_type": data.get("update_type"), "details": data.get("details")},
    })

def _on_disaster_resolved(data: dict) -> None:
    _publish({
        "service": "disaster", "event_type": "disaster.resolved",
        "severity": "INFO", "colour": SEVERITY_COLOUR["INFO"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Disaster resolved — {data.get('tracking_id', '')}",
        "message": data.get("resolution_notes", "The situation has been resolved."),
        "data": {"disaster_id": data.get("disaster_id"), "tracking_id": data.get("tracking_id"),
                 "resolution_notes": data.get("resolution_notes")},
    })

def _on_backup_requested(data: dict) -> None:
    resources = data.get("resources_needed") or ["Unspecified"]
    _publish({
        "service": "disaster", "event_type": "disaster.backup_requested",
        "severity": "CRITICAL", "colour": SEVERITY_COLOUR["CRITICAL"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Backup requested — {data.get('tracking_id', '')}",
        "message": f"Field unit requesting backup. Resources: {', '.join(resources)}",
        "data": {"disaster_id": data.get("disaster_id"), "tracking_id": data.get("tracking_id"),
                 "resources_needed": resources},
    })

def _on_unit_completed(data: dict) -> None:
    _publish({
        "service": "disaster", "event_type": "disaster.unit_completed",
        "severity": "INFO", "colour": SEVERITY_COLOUR["INFO"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Unit mission complete — {data.get('tracking_id', '')}",
        "message": "A response unit has completed its mission.",
        "data": {"disaster_id": data.get("disaster_id"), "tracking_id": data.get("tracking_id"),
                 "unit_id": data.get("unit_id")},
    })

def _on_reroute_triggered(data: dict) -> None:
    vehicles = data.get("vehicles_count") or len(data.get("vehicles", []))
    _publish({
        "service": "reroute", "event_type": "reroute.triggered",
        "severity": "HIGH", "colour": SEVERITY_COLOUR["HIGH"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": "Traffic reroute activated",
        "message": (
            f"{vehicles} vehicles rerouted across {len(data.get('routes', []))} routes. "
            f"Overflow: {data.get('overflow_count', 0)}."
        ),
        "data": {"disaster_id": data.get("disaster_id"), "plan_id": data.get("plan_id"),
                 "vehicles_count": vehicles, "overflow_count": data.get("overflow_count", 0),
                 "routes": data.get("routes", [])},
    })

def _on_route_updated(data: dict) -> None:
    reason = data.get("reason", "congestion")
    _publish({
        "service": "reroute", "event_type": "route.updated",
        "severity": "MEDIUM", "colour": SEVERITY_COLOUR["MEDIUM"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": f"Routes recalculated — {reason}",
        "message": f"Traffic routes updated due to {reason}.",
        "data": {"disaster_id": data.get("disaster_id"), "reason": reason,
                 "routes": data.get("routes", [])},
    })

def _on_disaster_cleared(data: dict) -> None:
    _publish({
        "service": "reroute", "event_type": "disaster.cleared",
        "severity": "INFO", "colour": SEVERITY_COLOUR["INFO"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": "Roads cleared — normal flow restored",
        "message": (
            f"Disaster cleared. {data.get('cleared_segments', 0)} road segment(s) reopened. "
            "Normal traffic flow has resumed."
        ),
        "data": {"disaster_id": data.get("disaster_id"),
                 "cleared_segments": data.get("cleared_segments")},
    })

def _on_disaster_reported(data: dict) -> None:
    """evaluation_queue — log only for now, future: auto-evaluate."""
    logger.info(
        f"[evaluation_queue] disaster.reported — "
        f"id={data.get('disaster_id')} type={data.get('type')}"
    )

# ---------------------------------------------------------------------------
# Master dispatch table
# ---------------------------------------------------------------------------

HANDLERS = {
    "disaster.evaluated":        _on_disaster_evaluated,
    "disaster.dispatched":       _on_disaster_dispatched,
    "disaster.verified":         _on_disaster_verified,
    "disaster.updated":          _on_disaster_updated,
    "disaster.resolved":         _on_disaster_resolved,
    "disaster.backup_requested": _on_backup_requested,
    "disaster.unit_completed":   _on_unit_completed,
    "reroute.triggered":         _on_reroute_triggered,
    "route.updated":             _on_route_updated,
    "disaster.cleared":          _on_disaster_cleared,
    "disaster.reported":         _on_disaster_reported,
}

ALL_QUEUES = [
    "notification_queue",
    "notification.reroute",
    "reroute_queue",
    "evaluation_queue",
]

# ---------------------------------------------------------------------------
# Unified Consumer
# ---------------------------------------------------------------------------

class UnifiedConsumer:
    """One process, one connection, consuming all queues."""

    def __init__(self):
        self.rabbitmq_url = RABBITMQ_URL
        self.connection = None
        self.channel = None
        signal.signal(signal.SIGINT, self._shutdown)
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
                self.channel = self.connection.channel()
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
                data = json.loads(body)
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
                    # Re-open channel after passive declare fails
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
        print(f"  Press Ctrl+C to stop")
        print(f"{'='*60}\n")

        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            self._shutdown(None, None)


if __name__ == "__main__":
    consumer = UnifiedConsumer()
    consumer.start()