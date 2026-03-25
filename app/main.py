# File: app/main.py
"""
ASE Emergency Services Backend - Main Application

UPDATED:
- Logging configuration on startup
- Redis fallback support
- 1 year access tokens
"""
import asyncio  
from fastapi import FastAPI, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api.v1 import user_auth
from app.api.v1 import emergency_team_auth
from app.api.v1 import live_map
from app.api.v1 import scenario_engine
from app.api.v1 import reroute
from app.api.v1 import disaster_report
from app.api.v1 import vehicles
from app.api.v1.disaster import router as disaster_router
from app.api.v1 import disaster_evaluation

from cache.redis_client import close_redis_connection
from app.providers.map_provider import MapProvider
from app.providers.traffic import TrafficProvider
from app.api.v1.live_map import set_live_map_providers
from app.socket.manager import sio
from app.workers.reroute_publisher import get_publisher
import socketio
from app.api.v1.emergency_unit import router as emergency_unit_router
from app.api.v1.deployment import router as deployment_router
from app.api.v1.disaster_evaluation import set_evaluation_providers
from app.api.v1.user_management import router as user_management_router

from app.api.v1.notifications_ws import router as notifications_router
from app.api.v1.notifications_ws import redis_listener                  

# Setup logging FIRST
setup_logging()
logger = logging.getLogger(__name__)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown events.
    """
    # Startup
    logger.info("=" * 70)
    logger.info(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"📍 Environment: {settings.ENVIRONMENT}")
    logger.info(f"🔒 Debug Mode: {settings.DEBUG}")
    logger.info(f"🔑 Access Token: {settings.ACCESS_TOKEN_EXPIRE_MINUTES} minutes ({settings.ACCESS_TOKEN_EXPIRE_MINUTES // 525600} year)")
    logger.info(f"🔄 Refresh Token: {settings.REFRESH_TOKEN_EXPIRE_DAYS} days ({settings.REFRESH_TOKEN_EXPIRE_DAYS // 365} years)")
    logger.info("=" * 70)

    map_provider = MapProvider(api_key=settings.MAPBOX_API_KEY)
    traffic_provider = TrafficProvider(api_key=settings.TRAFFIC_API_KEY)
    set_live_map_providers(map_provider, traffic_provider)
    set_evaluation_providers(map_provider, traffic_provider)
    logger.info("🗺️  Map and traffic providers initialized")

    # Connect RabbitMQ publisher
    publisher = get_publisher()
    await publisher.connect()
    if publisher.is_connected:
        logger.info("🐇 RabbitMQ publisher connected")
    else:
        logger.warning("⚠️  RabbitMQ publisher not connected — running in degraded mode (notifications disabled)")

    # Start Redis pub/sub listener for WebSocket notifications
    listener_task = asyncio.create_task(redis_listener())
    logger.info("📡 Redis notification listener started")

    yield

    # Cancel listener on shutdown
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    # Shutdown
    logger.info("=" * 70)
    logger.info("Shutting down...")
    await close_redis_connection()
    await traffic_provider.close()
    await publisher.close()
    logger.info("Cleanup complete")
    logger.info("=" * 70)


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    ASE Emergency Services Backend API
    
    ## Features
    
    * **User Authentication** - OTP-based registration and login
    * **Emergency Team Auth** - OTP registration + password login
    * **Emergency Requests** - Submit and track emergency requests
    * **Real-time Updates** - WebSocket notifications
    * **Multi-Department** - Medical, Police, Fire, IT support
    * **Long Sessions** - 1 year access tokens (no frequent re-login)
    * **Redis Fallback** - Automatic in-memory cache when Redis unavailable
    
    ## Authentication
    
    ### Users (Regular Citizens)
    - Register with phone number + OTP
    - Login with phone number + OTP
    - Access token valid for 1 year
    
    ### Emergency Teams (Responders)
    - Register with phone number + password + OTP
    - Login with email/phone + password (no OTP)
    - Access token valid for 1 year
    - Role-based access (Admin, Manager, Staff)
    - Department-specific access
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    debug=settings.DEBUG
)


# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Update in production with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler for unhandled errors.
    
    Logs the error and returns a generic error response.
    """
    logger.exception(f"Unhandled error: {str(exc)}")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred. Please try again later.",
            "details": str(exc) if settings.DEBUG else None
        }
    )


# Include routers
app.include_router(user_auth.router, prefix="/api/v1")
app.include_router(emergency_team_auth.router, prefix="/api/v1")
app.include_router(live_map.router, prefix="/api/v1")
app.include_router(scenario_engine.router, prefix="/api/v1")
app.include_router(reroute.router, prefix="/api/v1")
app.include_router(disaster_report.router, prefix="/api/v1")
app.include_router(disaster_router, prefix="/api/v1")
app.include_router(disaster_evaluation.router, prefix="/api/v1")

app.include_router(emergency_unit_router, prefix="/api/v1")
app.include_router(vehicles.router, prefix = "/api/v1")

app.include_router(deployment_router, prefix="/api/v1")
app.include_router(user_management_router, prefix="/api/v1")

# ── Notification router ─────────────────────────────────
app.include_router(notifications_router,prefix="/api/v1")  
# Serve demo page
from fastapi.responses import FileResponse
from pathlib import Path

@app.get("/demo", include_in_schema=False)
async def demo_page():
    """Serve the reroute live demo page."""
    demo_path = Path(__file__).parent.parent / "demo.html"
    return FileResponse(str(demo_path))

# Root endpoints
@app.get(
    "/",
    tags=["Root"],
    summary="API Root",
    description="Welcome endpoint with API information"
)
async def root():
    """
    API root endpoint.
    
    Returns basic API information and links.
    """
    return {
        "message": f"Welcome to {settings.APP_NAME} API",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "token_expiry": f"{settings.ACCESS_TOKEN_EXPIRE_MINUTES // 525600} year",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health"
    }


@app.get(
    "/health",
    tags=["Root"],
    summary="Health Check",
    description="Check if the API is running"
)
async def health_check():
    """
    Health check endpoint.
    
    Returns API health status including Redis status.
    """
    from cache.redis_client import check_redis_health
    
    redis_health = await check_redis_health()
    
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "redis": redis_health
    }


# ---------------------------------------------------------------------------
# Mount Socket.IO as ASGI middleware
# ---------------------------------------------------------------------------
# Wrap FastAPI app with Socket.IO so both share the same port.
# HTTP requests → FastAPI, WebSocket requests → Socket.IO
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)


# Development server runner
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:socket_app",  # Run socket_app, not app
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info" if not settings.DEBUG else "debug"
    )