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

    # Beat schedule — monitoring loop runs every 30 seconds (Phase 4a)
    beat_schedule={
        "monitor-traffic-conditions": {
            "task": "app.workers.tasks.monitor_traffic_conditions",
            "schedule": 30.0,  # seconds — matches Section 6.4 polling guidance
            "args": [],
        },
    },
)