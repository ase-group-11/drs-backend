# File: app/main.py
"""
ASE Emergency Services Backend - Main Application

UPDATED:
- Logging configuration on startup
- Redis fallback support
- 1 year access tokens
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api.v1 import api_router
from cache.redis_client import close_redis_connection

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
    
    yield
    
    # Shutdown
    logger.info("=" * 70)
    logger.info("Shutting down...")
    await close_redis_connection()
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
    * **Disaster Reporting** - Report disasters with location, severity, images
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


# Include API router
app.include_router(api_router, prefix="/api/v1")


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


# Development server runner
if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info" if not settings.DEBUG else "debug"
    )