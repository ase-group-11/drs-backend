from fastapi import APIRouter

from app.api.v1 import user_auth, emergency_team_auth, disasters

api_router = APIRouter()

# Register authentication routers from main
api_router.include_router(user_auth.router, prefix="/auth/users", tags=["user-auth"])
api_router.include_router(emergency_team_auth.router, prefix="/auth/emergency-teams", tags=["emergency-team-auth"])

# Register disaster reporting router
api_router.include_router(disasters.router, prefix="/disasters", tags=["disasters"])
