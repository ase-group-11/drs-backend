from celery import Celery
from app.core.config import settings



celery_app = Celery(
    "drs_reroute",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    beat_schedule={
        "monitor-traffic-conditions": {
            "task": "app.workers.tasks.monitor_traffic_conditions",
            # Was 30s (2,880 calls/day per active region — exceeds TomTom free tier
            # of 2,500 non-tile requests). 300s = ~288 calls/day, fits 2 concurrent
            # active disasters within the free tier limit.
            "schedule": 300.0,
            "args": [],
        },
        "warm-traffic-cache": {
            "task": "app.workers.tasks.warm_traffic_cache",
            # Runs 2s ahead of monitor so the cache is always warm when monitor fires.
            # Was 25s — reduced in step with monitor to conserve TomTom API quota.
            "schedule": 298.0,
            "args": [],
        },
        "auto-evaluate-pending-reports": {
            "task": "app.workers.tasks.auto_evaluate_pending_reports",
            "schedule": 60.0,
            "args": [],
        },
        "periodic-reassess-disasters": {
            "task": "app.workers.tasks.periodic_reassess_disasters",
            "schedule": 900.0,
            "args": [],
        },
    },
)