# File: app/api/v1/notifications_ws.py
"""
WebSocket Notification Endpoint + Redis pub/sub listener.

Architecture:
                                        ┌─────────────────────────┐
    notification_consumer.py            │  FastAPI event loop      │
    (blocking pika thread)              │                          │
         │                             │  redis_listener()        │
         │  redis.publish("app_alerts")│       │                  │
         └────────────────────────────►│       ▼                  │
                                        │  broadcast()             │
                                        │       │                  │
                                        │       ▼                  │
                                        │  WebSocket clients       │
                                        └─────────────────────────┘

redis_listener() is started as an asyncio background task inside the
FastAPI lifespan (see main.py).  It uses redis.asyncio — the async
variant of the redis-py library already in requirements.txt — so it
runs natively in the same event loop as the WebSocket handlers.

WebSocket URL:
    ws://<host>/api/v1/ws/notifications
"""

import asyncio
import json
import logging
import os
from typing import Set

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("notifications_ws")

router = APIRouter(tags=["Notifications"])

REDIS_URL     = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHANNEL = "app_alerts"

# In-memory set of active WebSocket connections.
# All access is from within the single async event loop — no locking needed.
connected_clients: Set[WebSocket] = set()


# ══════════════════════════════════════════════════════════════
# Broadcaster
# ══════════════════════════════════════════════════════════════

async def broadcast(message: str) -> None:
    """
    Send a raw JSON string to every connected WebSocket client.
    Dead connections are silently removed.
    """
    dead: Set[WebSocket] = set()
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)
    if dead:
        logger.info(
            f"Pruned {len(dead)} dead connection(s). "
            f"Active clients: {len(connected_clients)}"
        )


# ══════════════════════════════════════════════════════════════
# Redis listener — one long-running async task
# ══════════════════════════════════════════════════════════════

async def redis_listener() -> None:
    """
    Subscribe to Redis 'app_alerts' channel and forward every
    message to all connected WebSocket clients.

    Started once in app/main.py lifespan:
        asyncio.create_task(redis_listener())

    Reconnects automatically if Redis drops.
    """
    logger.info(f"Redis listener starting — channel: {REDIS_CHANNEL}")

    while True:
        try:
            client = aioredis.from_url(REDIS_URL, decode_responses=True)
            pubsub  = client.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)
            logger.info(f"Subscribed to Redis channel: {REDIS_CHANNEL}")

            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue

                data = raw["data"]

                # Validate JSON before forwarding — drop malformed frames
                try:
                    json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Received non-JSON on app_alerts — skipping")
                    continue

                await broadcast(data)

        except asyncio.CancelledError:
            logger.info("Redis listener cancelled — shutting down cleanly")
            return
        except Exception as exc:
            logger.error(f"Redis listener error: {exc} — reconnecting in 3 s")
            await asyncio.sleep(3)


# ══════════════════════════════════════════════════════════════
# WebSocket endpoint
# ══════════════════════════════════════════════════════════════

@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket) -> None:
    """
    Real-time notification endpoint for frontend clients.

    Connect:
        ws://localhost:8000/api/v1/ws/notifications

    The client receives JSON alert envelopes automatically whenever
    any service publishes to the Redis 'app_alerts' channel.

    Alert envelope shape:
    {
        "service":    "disaster",
        "event_type": "disaster.dispatched" | "disaster.verified" | ...,
        "severity":   "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "INFO",
        "colour":     "green" | "blue" | "yellow" | "orange" | "red",
        "title":      "<short headline>",
        "message":    "<human readable description>",
        "data":       { ... },   // event-specific payload
        "timestamp":  "<ISO-8601>"
    }
    """
    await websocket.accept()
    connected_clients.add(websocket)
    logger.info(f"Client connected. Active: {len(connected_clients)}")

    try:
        while True:
            # Keep connection alive. Inbound frames are discarded —
            # the client only needs to receive, not send.
            await asyncio.wait_for(websocket.receive_text(), timeout=60)
    except asyncio.TimeoutError:
        pass        # no data for 60 s — normal, loop back
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug(f"WebSocket error: {exc}")
    finally:
        connected_clients.discard(websocket)
        logger.info(f"Client disconnected. Active: {len(connected_clients)}")