# File: app/services/rabbitmq_service.py
"""
RabbitMQ Publisher Service.

Manages connection to RabbitMQ, creates exchange and queues,
and publishes disaster events for async processing.

Exchange: disaster_events (topic)
Routing Keys:
  - disaster.reported  → new disaster created (goes to ALL queues)
  - disaster.evaluated → severity scored (goes to coordination + reroute)
  - disaster.updated   → status changed (goes to notification)
  - disaster.resolved  → disaster over (goes to notification + reroute)

Queues:
  - evaluation_queue    → Disaster Evaluation Service
  - coordination_queue  → Emergency Coordination Service
  - notification_queue  → Notification Service
  - reroute_queue       → Re-Route Service
"""

import os
import json
import logging
import pika
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "disaster_events"

# Queue → Routing key bindings
QUEUE_BINDINGS = {
    "evaluation_queue": ["disaster.verified"],
    "coordination_queue": ["disaster.verified", "disaster.evaluated", "disaster.backup_requested"],
    "notification_queue": ["disaster.dispatched", "disaster.verified", "disaster.evaluated", "disaster.updated",
                           "disaster.resolved", "disaster.backup_requested", "disaster.unit_completed"],
    "reroute_queue": ["disaster.verified", "disaster.evaluated", "disaster.resolved", "disaster.unit_completed"],
}


class RabbitMQService:
    """Publishes disaster events to RabbitMQ topic exchange."""

    def __init__(self):
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.channel.Channel] = None
        self.rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

    def connect(self):
        """Establish connection to RabbitMQ and setup exchange + queues."""
        try:
            params = pika.URLParameters(self.rabbitmq_url)
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()

            # Create topic exchange
            self.channel.exchange_declare(
                exchange=EXCHANGE_NAME,
                exchange_type="topic",
                durable=True,
            )

            # Create queues and bind to routing keys
            for queue_name, routing_keys in QUEUE_BINDINGS.items():
                self.channel.queue_declare(queue=queue_name, durable=True)
                for routing_key in routing_keys:
                    self.channel.queue_bind(
                        exchange=EXCHANGE_NAME,
                        queue=queue_name,
                        routing_key=routing_key,
                    )

            logger.info("RabbitMQ connected. Exchange and queues ready.")

        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            self.connection = None
            self.channel = None

    def _ensure_connection(self):
        """Reconnect if connection is lost."""
        if not self.connection or self.connection.is_closed:
            self.connect()
        if not self.channel or self.channel.is_closed:
            self.connect()

    def publish(self, routing_key: str, message: Dict[str, Any]):
        """
        Publish a message to the disaster_events exchange.

        Args:
            routing_key: e.g. 'disaster.reported', 'disaster.resolved'
            message: dict payload (will be JSON-serialized)
        """
        try:
            self._ensure_connection()

            if not self.channel:
                logger.warning(f"RabbitMQ not available. Skipping publish: {routing_key}")
                return False

            # Add timestamp to message
            message["published_at"] = datetime.utcnow().isoformat()
            message["event_type"] = routing_key

            body = json.dumps(message, default=str)

            self.channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # persistent message
                    content_type="application/json",
                ),
            )

            logger.info(f"Published to RabbitMQ: {routing_key} → {message.get('disaster_id', 'unknown')}")
            return True

        except Exception as e:
            logger.error(f"Failed to publish to RabbitMQ: {e}")
            # Reset connection so next call will reconnect
            self.connection = None
            self.channel = None
            return False

    def close(self):
        """Close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.info("RabbitMQ connection closed.")
        except Exception as e:
            logger.error(f"Error closing RabbitMQ: {e}")


# ── Singleton instance ──
_rabbitmq_service: Optional[RabbitMQService] = None


def get_rabbitmq_service() -> RabbitMQService:
    """Get or create singleton RabbitMQ service."""
    global _rabbitmq_service
    if _rabbitmq_service is None:
        _rabbitmq_service = RabbitMQService()
        _rabbitmq_service.connect()
    return _rabbitmq_service


# ── Helper functions for easy publishing ──

def publish_disaster_reported(disaster_data: Dict[str, Any]) -> bool:
    """Publish disaster.reported event — triggers evaluation, coordination, notification."""
    service = get_rabbitmq_service()
    return service.publish("disaster.reported", {
        "disaster_id": disaster_data.get("disaster_id"),
        "tracking_id": disaster_data.get("tracking_id"),
        "type": disaster_data.get("type"),
        "severity": disaster_data.get("severity"),
        "location": disaster_data.get("location"),
        "location_address": disaster_data.get("location_address"),
        "description": disaster_data.get("description"),
        "people_affected": disaster_data.get("people_affected"),
        "multiple_casualties": disaster_data.get("multiple_casualties"),
        "structural_damage": disaster_data.get("structural_damage"),
        "road_blocked": disaster_data.get("road_blocked"),
        "assigned_to_id": disaster_data.get("assigned_to_id"),
        "assigned_department": disaster_data.get("assigned_department"),
        "created_by_id": disaster_data.get("created_by_id"),
    })


def publish_disaster_evaluated(disaster_data: Dict[str, Any]) -> bool:
    """Publish disaster.evaluated event — triggers coordination, notification + reroute."""
    service = get_rabbitmq_service()
    return service.publish("disaster.evaluated", {
        "disaster_id": disaster_data.get("disaster_id"),
        "tracking_id": disaster_data.get("tracking_id"),
        "severity": disaster_data.get("severity"),
        "confidence": disaster_data.get("confidence"),
        "recommended_services": disaster_data.get("recommended_services"),
        "trigger_deploy": disaster_data.get("trigger_deploy"),
        "trigger_reroute": disaster_data.get("trigger_reroute"),
        "trigger_evacuation": disaster_data.get("trigger_evacuation"),
        "flag": disaster_data.get("flag"),
        "strategy_used": disaster_data.get("strategy_used"),
        "evaluated_at": disaster_data.get("evaluated_at"),
        "impact_radius_km": disaster_data.get("impact_radius_km"),
        "estimated_population": disaster_data.get("estimated_population"),
        "affected_roads": disaster_data.get("affected_roads"),
        "affected_facilities": disaster_data.get("affected_facilities"),
    })


def publish_disaster_updated(disaster_data: Dict[str, Any]) -> bool:
    """Publish disaster.updated event — triggers notification."""
    service = get_rabbitmq_service()
    return service.publish("disaster.updated", {
        "disaster_id": disaster_data.get("disaster_id"),
        "tracking_id": disaster_data.get("tracking_id"),
        "update_type": disaster_data.get("update_type"),
        "details": disaster_data.get("details"),
    })


def publish_reporter_notification(notification_data: Dict[str, Any]) -> bool:
    """
    Publish a reporter-targeted notification to the notification queue.

    Routing key: disaster.updated — already bound to notification_queue.

    Used by the evaluation service for:
    - REPORT_RECEIVED: "Your report has been received and evaluated."
    - FALSE_ALARM_WARNING: "Your report was classified as a false alarm."
    """
    service = get_rabbitmq_service()
    return service.publish("disaster.updated", {
        "disaster_id": notification_data.get("disaster_id"),
        "tracking_id": notification_data.get("tracking_id"),
        "update_type": "reporter_notification",
        "notification_type": notification_data.get("notification_type"),
        "user_id": notification_data.get("user_id"),
        "severity": notification_data.get("severity"),
        "flag": notification_data.get("flag"),
        "response_scale": notification_data.get("response_scale"),
        "message": notification_data.get("message"),
    })


def publish_disaster_resolved(disaster_data: Dict[str, Any]) -> bool:
    """Publish disaster.resolved event — triggers notification + reroute restore."""
    service = get_rabbitmq_service()
    return service.publish("disaster.resolved", {
        "disaster_id": disaster_data.get("disaster_id"),
        "tracking_id": disaster_data.get("tracking_id"),
        "resolution_notes": disaster_data.get("resolution_notes"),
        "resolved_time": disaster_data.get("resolved_time"),
    })