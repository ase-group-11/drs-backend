# File: app/main.py
"""
ASE Emergency Services Backend - Main Application

FastAPI application with:
- User authentication (OTP-based)
- Emergency team authentication (password-based)
- Emergency request management
- Real-time notifications
- RBAC (Role-Based Access Control)
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.core.config import settings
from app.api.v1 import auth
from app.db.redis_client import close_redis_connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown events.
    """
    # Startup
    print(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"📍 Environment: {settings.ENVIRONMENT}")
    print(f"🔒 Debug Mode: {settings.DEBUG}")
    
    yield
    
    # Shutdown
    print("🛑 Shutting down...")
    await close_redis_connection()
    print("✅ Cleanup complete")


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    ASE Emergency Services Backend API
    
    ## Features
    
    * **User Authentication** - OTP-based registration and login
    * **Emergency Team Auth** - Password-based authentication with RBAC
    * **Emergency Requests** - Submit and track emergency requests
    * **Real-time Updates** - WebSocket notifications
    * **Multi-Department** - Medical, Police, Fire, IT support
    
    ## Authentication
    
    ### Users (Regular Citizens)
    - Register with phone number
    - Receive OTP via SMS
    - Login with OTP
    
    ### Emergency Teams (Responders)
    - Login with phone number + password
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
    # In production, log this to a logging service
    print(f"❌ Unhandled error: {str(exc)}")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred. Please try again later.",
            "details": str(exc) if settings.DEBUG else None
        }
    )


# Include routers
app.include_router(auth.router, prefix="/api/v1")


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
    
    Returns API health status.
    """
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT
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