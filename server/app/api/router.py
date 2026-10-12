from fastapi import APIRouter

from app.api.routes import auth, dashboard, health, hotel, reservations, rooms

api_router: APIRouter = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(hotel.router)
api_router.include_router(reservations.router)
api_router.include_router(rooms.router)
