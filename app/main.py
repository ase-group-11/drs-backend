# File: app/main.py
"""
Dublin Disaster Response System — FastAPI Application Entry Point

Startup sequence:
  1. Logging configured
  2. Mapbox + TomTom providers initialised (used by live map + evaluation)
  3. RabbitMQ publisher connected (degraded mode if unavailable)
  4. Redis pub/sub listener started (WebSocket notification delivery)

Shutdown sequence:
  1. Redis listener task cancelled
  2. Redis connection closed
  3. TomTom HTTP session closed
  4. RabbitMQ publisher closed
"""

import asyncio
import logging, socketio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

# ── Core ──────────────────────────────────────────────────────────────────────
from app.core.config import settings
from app.core.logging_config import setup_logging

# ── Infrastructure ────────────────────────────────────────────────────────────
from cache.redis_client import close_redis_connection
from app.providers.map_provider import MapProvider
from app.providers.traffic import TrafficProvider
from app.workers.reroute_publisher import get_publisher
from app.socket.manager import sio

# ── Provider initialisers (called at startup) ─────────────────────────────────
from app.api.v1.live_map import set_live_map_providers
from app.api.v1.disaster_evaluation import set_evaluation_providers

# ── WebSocket / Redis listener ────────────────────────────────────────────────
from app.api.v1.notifications_ws import redis_listener
from app.api.v1 import chat                                               # Disaster group chat

# ── Auth routers ──────────────────────────────────────────────────────────────
from app.api.v1 import user_auth           # UC1: Citizen OTP auth
from app.api.v1 import emergency_team_auth # UC1: ERT password + OTP auth

# ── Disaster lifecycle routers ────────────────────────────────────────────────
from app.api.v1 import disaster_report     # UC2: Citizen disaster reports
from app.api.v1.disaster import router as disaster_router  # Verified disaster CRUD
from app.api.v1 import disaster_evaluation # UC5: AI evaluation + dispatch trigger
from app.api.v1 import scenario_engine     # Dev/test: pre-built disaster scenarios

# ── Emergency response routers ────────────────────────────────────────────────
from app.api.v1.emergency_unit import router as emergency_unit_router  # Unit CRUD + availability
from app.api.v1.deployment import router as deployment_router          # UC6 existing: dispatch + status
from app.api.v1 import deploy              # UC6 new: suggested units, GPS, routes, recall

# ── Traffic + evacuation routers ─────────────────────────────────────────────
from app.api.v1 import reroute             # UC7: Traffic rerouting
from app.api.v1 import evacuation          # UC8: Evacuation planning

# ── Map + real-time routers ───────────────────────────────────────────────────
from app.api.v1 import live_map            # UC4: Live disaster map
from app.api.v1 import vehicles            # Vehicle trip registration (map overlay)
from app.api.v1.incident_log import router as incident_log_router  # Audit / incident log

# ── User & notification routers ──────────────────────────────────────────────
from app.api.v1.user_management import router as user_management_router  # Admin user CRUD
from app.api.v1.notifications_ws import router as notifications_router   # WebSocket alerts
from app.workers.reroute_publisher import get_publisher

from app.db.session import get_db


# ── Configure logging before anything else ────────────────────────────────────
setup_logging()
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Lifespan — startup + graceful shutdown
# ═════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ───────────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"📍 Environment : {settings.ENVIRONMENT}")
    logger.info(f"🔒 Debug Mode  : {settings.DEBUG}")
    logger.info(
        f"🔑 Access Token : {settings.ACCESS_TOKEN_EXPIRE_MINUTES} min "
        f"({settings.ACCESS_TOKEN_EXPIRE_MINUTES // 525600} year)"
    )
    logger.info(
        f"🔄 Refresh Token: {settings.REFRESH_TOKEN_EXPIRE_DAYS} days "
        f"({settings.REFRESH_TOKEN_EXPIRE_DAYS // 365} years)"
    )
    logger.info("=" * 70)

    # 1. Map + traffic providers (Mapbox + TomTom)
    #    Shared by live map, disaster evaluation, and reroute services.
    map_provider     = MapProvider(api_key=settings.MAPBOX_API_KEY)
    traffic_provider = TrafficProvider(api_key=settings.TRAFFIC_API_KEY)
    set_live_map_providers(map_provider, traffic_provider)
    set_evaluation_providers(map_provider, traffic_provider)
    logger.info("🗺️  Mapbox + TomTom providers initialised")

    # 2. RabbitMQ publisher (used by deploy, reroute, evacuation services)
    #    If the broker is unreachable, the app runs in degraded mode —
    #    all DB operations succeed but RabbitMQ events are silently dropped.
    publisher = get_publisher()
    await publisher.connect()
    if publisher.is_connected:
        logger.info("🐇 RabbitMQ publisher connected")
    else:
        logger.warning(
            "⚠️  RabbitMQ unavailable — running in degraded mode "
            "(notifications disabled, all DB ops still work)"
        )

    # 3. Redis pub/sub listener — delivers alerts to connected WebSocket clients
    listener_task = asyncio.create_task(redis_listener())
    logger.info("📡 Redis notification listener started")

    yield  # ── application runs here ──────────────────────────────────────────

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("🛑 Shutting down...")

    # Helper: run a cleanup coroutine with a hard timeout so that a hanging
    # remote connection (Redis / RabbitMQ / aiohttp on Azure) never blocks
    # the reload indefinitely.
    async def _safe_close(coro, label: str, timeout: float = 3.0) -> None:
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            logger.debug(f"✅ {label} closed")
        except asyncio.TimeoutError:
            logger.warning(f"⚠️  {label} close timed out after {timeout}s — skipping")
        except Exception as exc:
            logger.warning(f"⚠️  {label} close error — {exc}")

    # Cancel the Redis pub/sub listener task.
    # Give it slightly longer than its own internal wait_for(timeout=5.0) so
    # that the CancelledError always wins on Python 3.9/3.10 where wait_for
    # can delay propagation until the inner timeout fires.
    listener_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(listener_task), timeout=6.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    # Close remote connections — all guarded so a network blip never stalls
    # the server for longer than the sum of these timeouts (~9 s worst-case).
    await _safe_close(close_redis_connection(),   "Redis connection",   timeout=3.0)
    await _safe_close(traffic_provider.close(),   "TomTom provider",    timeout=3.0)
    await _safe_close(publisher.close(),          "RabbitMQ publisher", timeout=3.0)

    logger.info("✅ Cleanup complete")
    logger.info("=" * 70)


# ═════════════════════════════════════════════════════════════════════════════
# FastAPI app
# ═════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    Dublin Disaster Response System — Backend API

    ## Use Cases
    * **UC1** — User + Emergency Team Authentication (OTP / password)
    * **UC2** — Citizen Disaster Reporting
    * **UC4** — Live Disaster Map (Mapbox + TomTom + PostGIS)
    * **UC5** — Disaster Evaluation (Rules engine + XGBoost ensemble)
    * **UC6** — Deploy Services (dispatch, GPS tracking, recall)
    * **UC7** — Re-Route Traffic (TomTom + Socket.IO)
    * **UC8** — Plan Evacuation (zone routing + shelter allocation)

    ## Infrastructure
    * **Auth** — JWT (1 year access tokens), Argon2id password hashing
    * **DB** — PostgreSQL + PostGIS on Azure
    * **Cache** — Redis (graceful in-memory fallback)
    * **Events** — RabbitMQ (degraded mode when unavailable)
    * **Real-time** — Socket.IO + WebSocket push notifications
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    debug=settings.DEBUG,
)


# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: lock down to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global error handler ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error":   "InternalServerError",
            "message": "An unexpected error occurred. Please try again later.",
            "details": str(exc) if settings.DEBUG else None,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# Router registration
# Order matters: FastAPI matches routes top-to-bottom within each router,
# so there are no ordering issues here — each router has its own prefix.
# ═════════════════════════════════════════════════════════════════════════════

# ── Authentication ────────────────────────────────────────────────────────────
app.include_router(user_auth.router,           prefix="/api/v1")  # /auth/*
app.include_router(emergency_team_auth.router, prefix="/api/v1")  # /emergency-team/*

# ── Disaster lifecycle ────────────────────────────────────────────────────────
app.include_router(disaster_report.router,     prefix="/api/v1")  # /disaster-reports/*
app.include_router(disaster_router,            prefix="/api/v1")  # /disasters/*
app.include_router(disaster_evaluation.router, prefix="/api/v1")  # /disaster-evaluation/*
app.include_router(scenario_engine.router,     prefix="/api/v1")  # /scenarios/*

# ── Emergency units + deployments (UC6) ──────────────────────────────────────
app.include_router(emergency_unit_router,      prefix="/api/v1")  # /emergency-units/*
app.include_router(deployment_router,          prefix="/api/v1")  # /deployments/* (existing)
app.include_router(deploy.router,              prefix="/api/v1")  # /disasters/*/suggested-units, /deployments/*/location|route|recall, /routes/calculate

# ── Traffic + evacuation ──────────────────────────────────────────────────────
app.include_router(reroute.router,             prefix="/api/v1")  # /reroute/*
app.include_router(evacuation.router,          prefix="/api/v1")  # /evacuations/*

# ── Map + vehicles ────────────────────────────────────────────────────────────
app.include_router(live_map.router,            prefix="/api/v1")  # /live-map/*
app.include_router(vehicles.router,            prefix="/api/v1")  # /vehicles/*

# ── Logging + admin ───────────────────────────────────────────────────────────
app.include_router(incident_log_router,        prefix="/api/v1")  # /incident-log/*
app.include_router(user_management_router,     prefix="/api/v1")  # /users/*

# ── Real-time notifications ───────────────────────────────────────────────────
app.include_router(notifications_router,       prefix="/api/v1")  # /ws/notifications
app.include_router(chat.router,                prefix="/api/v1")  # /ws/chat/{disaster_id}, /chat/{disaster_id}/history

# ── Dev-only seed endpoint (never registered in production) ──────────────────
if settings.ENVIRONMENT != "production":
    from app.api.v1 import dev_seed
    app.include_router(dev_seed.router, prefix="/api/v1")  # /dev/seed


# ═════════════════════════════════════════════════════════════════════════════
# Utility endpoints
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/demo", include_in_schema=False)
async def demo_page():
    """Serve the reroute live demo HTML page (dev only)."""
    demo_path = Path(__file__).parent.parent / "demo.html"
    return FileResponse(str(demo_path))


@app.get("/", tags=["Root"], summary="API health check")
async def root():
    """Returns API status and version. Use /docs for full documentation."""
    return {
        "status":      "online",
        "app":         settings.APP_NAME,
        "version":     settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "docs":        "/docs",
    }


# @app.get(
#     "/health",
#     tags=["Root"],
#     summary="Health Check",
#     description="Check status of all major subsystems"
# )
# async def health_check():
#     """
#     Returns health status.
#     """
#     return {"status": "ok"}


@app.get("/health/live", tags=["Health"], include_in_schema=False)
async def liveness():
    """Kubernetes liveness probe — just proves the process is alive."""
    return {"status": "ok"}

@app.get(
    "/health/ready",
    tags=["Health"],
    summary="Readiness Check",
    description="Check status of all major subsystems"
)
async def readiness_check():
    """
    Kubernetes readiness probe — proves the app can serve traffic.
    Uses the existing connection pool (no new connections opened).
    Returns 503 if any critical dependency is unhealthy.
    """
    import time
    from sqlalchemy import text

    health = {
        "status": "ok",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": {}
    }

    # ── PostgreSQL — reuse pool, don't open a new session ─────────
    try:
        from app.db.session import async_session_factory
        async with async_session_factory() as session:
            await asyncio.wait_for(session.scalar(text("SELECT 1")), timeout=3.0)
        health["services"]["postgresql"] = {"status": "ok"}
    except asyncio.TimeoutError:
        health["services"]["postgresql"] = {"status": "error", "detail": "timeout"}
        health["status"] = "degraded"
    except Exception as e:
        health["services"]["postgresql"] = {"status": "error", "detail": str(e)}
        health["status"] = "degraded"

    # ── Redis ─────────────────────────────────────────────────────
    try:
        from cache.redis_client import check_redis_health
        redis_health = await check_redis_health()
        health["services"]["redis"] = redis_health
        if redis_health.get("status") != "healthy":
            health["status"] = "degraded"
    except Exception as e:
        health["services"]["redis"] = {"status": "error", "detail": str(e)}
        health["status"] = "degraded"

    # ── RabbitMQ — connection state check only, no new connection ─
    try:
        publisher = get_publisher()
        if publisher.is_connected:
            health["services"]["rabbitmq"] = {"status": "ok"}
        else:
            # Degraded but not critical — app still functions without it
            health["services"]["rabbitmq"] = {"status": "degraded", "detail": "not connected"}
    except Exception as e:
        health["services"]["rabbitmq"] = {"status": "error", "detail": str(e)}

    # ── TomTom ────────────────────────────────────────────────────
    try:
        from app.providers.integration_service import get_integration_service
        integration = get_integration_service()
        circuit_state = str(integration.circuit_breaker.current_state)
        health["services"]["tomtom"] = {
            "status": "ok" if circuit_state == "closed" else "degraded",
            "circuit_breaker": circuit_state
        }
    except Exception as e:
        health["services"]["tomtom"] = {"status": "error", "detail": str(e)}

    # Only PostgreSQL is truly critical for readiness
    if health["services"].get("postgresql", {}).get("status") != "ok":
        from fastapi import Response
        return JSONResponse(status_code=503, content=health)

    return health

socket_app = socketio.ASGIApp(sio, other_asgi_app = app)