from fastapi import APIRouter

from app.api.v1 import auth
# from app.api.v1 import users      <-- Add future files here
# from app.api.v1 import payments   <-- Add future files here

api_router = APIRouter()

# Register the routers
api_router.include_router(auth.router)
# api_router.include_router(users.router)
# api_router.include_router(payments.router)