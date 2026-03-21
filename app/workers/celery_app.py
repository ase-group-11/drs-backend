"""
app/workers/celery_app.py

Celery application configuration for background tasks.

Used by:
  - Phase 4a: Monitoring loop — periodic traffic condition polling
  - Phase 4a: Predictive congestion recalculation cycles

Broker + backend: Redis (same instance used by Socket.IO adapter).
"""

from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "drs_reroute",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task behaviour
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,

    # Beat schedule
    beat_schedule={
        # Congestion monitoring every 30s (Phase 4a)
        "monitor-traffic-conditions": {
            "task": "app.workers.tasks.monitor_traffic_conditions",
            "schedule": 30.0,
            "args": [],
        },
        # Cache pre-warmer every 25s — keeps traffic cache fresh before
        # the 30s TTL expires so monitoring loop never waits for TomTom
        "warm-traffic-cache": {
            "task": "app.workers.tasks.warm_traffic_cache",
            "schedule": 25.0,
            "args": [],
        },
    },
)