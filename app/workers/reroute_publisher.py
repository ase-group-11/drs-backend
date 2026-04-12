"""
app/workers/publisher.py

RabbitMQ event publisher for the ReRoute Service.

Publishes reroute lifecycle events to RabbitMQ so the Notification Service
(and any other consumer) can process them asynchronously and independently.

Exchange:  reroute.events  (type: topic, durable)
Routing keys:
  reroute.triggered       — initial reroute pipeline complete
  route.updated           — routes recalculated (congestion / override)
  disaster.cleared        — disaster resolved, normal flow restored

Consumer (NotificationService, owned by teammate) subscribes to these
routing keys and handles SMS / push / Socket.IO delivery.
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import aio_pika
from aio_pika import ExchangeType

from app.core.config import settings

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "reroute.events"


class ReroutePublisher:
    """
    Publishes reroute domain events to RabbitMQ.

    Lifecycle:
        publisher = ReroutePublisher()
        await publisher.connect()
        await publisher.publish_reroute_triggered(...)
        await publisher.close()

    In FastAPI, connect() is called in the lifespan startup hook.
    """

    def __init__(self, url: Optional[str] = None):
        self.url = url or settings.RABBITMQ_URL
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractChannel] = None
        self._exchange: Optional[aio_pika.abc.AbstractExchange] = None

    async def connect(self) -> None:
        """Open connection, channel, and declare the topic exchange."""
        try:
            self._connection = await aio_pika.connect_robust(self.url)
            self._channel    = await self._connection.channel()
            self._exchange   = await self._channel.declare_exchange(
                EXCHANGE_NAME,
                ExchangeType.TOPIC,
                durable=True,
            )

            # notification.reroute — reroute/traffic events
            reroute_queue = await self._channel.declare_queue(
                "notification.reroute",
                durable=True,
            )
            for routing_key in ["reroute.triggered", "route.updated", "disaster.cleared"]:
                await reroute_queue.bind(self._exchange, routing_key=routing_key)

            # evacuation_queue — evacuation plan activated
            evac_queue = await self._channel.declare_queue(
                "evacuation_queue",
                durable=True,
            )
            await evac_queue.bind(self._exchange, routing_key="evacuation.triggered")

            logger.info(f"ReroutePublisher: connected to {self.url}, exchange={EXCHANGE_NAME}")
        except Exception as e:
            logger.error(f"ReroutePublisher: failed to connect — {e}")
            # Allow app to start in degraded mode (no MQ)
            self._exchange = None

    async def close(self) -> None:
        """Close channel and connection gracefully."""
        try:
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
            logger.info("ReroutePublisher: connection closed")
        except Exception as e:
            logger.warning(f"ReroutePublisher: error during close — {e}")

    @property
    def is_connected(self) -> bool:
        return self._exchange is not None

    # -------------------------------------------------------------------------
    # Public publish methods
    # -------------------------------------------------------------------------

    async def publish_reroute_triggered(
        self,
        disaster_id: str,
        plan_id: str,
        vehicles: list,
        route_assignments: Dict[str, str],
        routes: list,
        overflow_count: int = 0,
        location: Optional[Dict] = None,
        tracking_id: str = "",
    ) -> bool:
        """
        Publish event when reroute pipeline completes successfully.

        Routing key: reroute.triggered

        Payload:
            disaster_id, plan_id, vehicles, route_assignments, routes,
            overflow_count, timestamp
        """
        return await self._publish(
            routing_key="reroute.triggered",
            payload={
                "event": "reroute.triggered",
                "disaster_id": disaster_id,
                "plan_id": plan_id,
                "vehicles": vehicles,
                "route_assignments": route_assignments,
                "routes": _slim_routes(routes),
                "overflow_count": overflow_count,
                "location": location or {},
                "tracking_id": tracking_id,
                "timestamp": _now(),
            },
        )

    async def publish_route_updated(
        self,
        disaster_id: str,
        reason: str,
        vehicles: list,
        route_assignments: Dict[str, str],
        routes: list,
    ) -> bool:
        """
        Publish event when routes are recalculated due to congestion or override.

        Routing key: route.updated

        Args:
            reason: 'congestion' | 'operator_override'
        """
        return await self._publish(
            routing_key="route.updated",
            payload={
                "event": "route.updated",
                "disaster_id": disaster_id,
                "reason": reason,
                "vehicles": vehicles,
                "route_assignments": route_assignments,
                "routes": _slim_routes(routes),
                "timestamp": _now(),
            },
        )

    async def publish_all_clear(
        self,
        disaster_id: str,
        users: list,
        cleared_segments: int = 0,
    ) -> bool:
        """
        Publish event when a disaster is cleared and normal flow is restored.

        Routing key: disaster.cleared
        """
        return await self._publish(
            routing_key="disaster.cleared",
            payload={
                "event": "disaster.cleared",
                "disaster_id": disaster_id,
                "users": users,
                "cleared_segments": cleared_segments,
                "message": "Roads have been cleared. Normal traffic flow has resumed.",
                "timestamp": _now(),
            },
        )
    
    async def publish_evacuation_triggered(
        self,
        disaster_id: str,
        plan_id: str,
        vehicles: list,
        routes: list,
        total_users: int = 0,
        location: str = "",
    ) -> bool:
        return await self._publish(
            routing_key="evacuation.triggered",
            payload={
                "event":       "evacuation.triggered",
                "disaster_id": disaster_id,
                "plan_id":     plan_id,
                "vehicles":    vehicles,
                "routes":      _slim_routes(routes),
                "total_users": total_users,
                "location_address": location,
                "timestamp":   _now(),
            },
        )

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    async def _publish(self, routing_key: str, payload: Dict[str, Any]) -> bool:
        """
        Serialize and publish a message to the exchange.

        Returns True on success, False if MQ is unavailable (degraded mode).
        The caller should log but not fail on False — notifications are
        best-effort and must not block the reroute pipeline.
        """
        if not self.is_connected:
            logger.warning(
                f"ReroutePublisher: not connected — dropping event '{routing_key}' "
                f"for disaster={payload.get('disaster_id')}"
            )
            return False

        try:
            message = aio_pika.Message(
                body=json.dumps(payload).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await self._exchange.publish(message, routing_key=routing_key)
            logger.info(
                f"ReroutePublisher: published '{routing_key}' "
                f"disaster={payload.get('disaster_id')}"
            )
            return True
        except Exception as e:
            logger.error(f"ReroutePublisher: failed to publish '{routing_key}' — {e}")
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slim_routes(routes: list) -> list:
    """
    Strip heavy geometry from routes before publishing.

    Notifications only need route_id and travel_time — no need to
    put full GeoJSON point arrays through the message queue.
    """
    return [
        {
            "route_id": r.get("route_id"),
            "travel_time_seconds": r.get("travel_time_seconds"),
            "length_meters": r.get("length_meters"),
            "traffic_delay_seconds": r.get("traffic_delay_seconds"),
        }
        for r in routes
    ]


# ---------------------------------------------------------------------------
# Module-level singleton (wired in FastAPI lifespan)
# ---------------------------------------------------------------------------

_publisher: Optional[ReroutePublisher] = None


def get_publisher() -> ReroutePublisher:
    """Get or create the module-level ReroutePublisher singleton."""
    global _publisher
    if _publisher is None:
        _publisher = ReroutePublisher()
    return _publisher