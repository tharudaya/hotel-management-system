from fastapi import APIRouter

from app.api.routes import (
    account,
    admin_audit,
    admin_roles,
    admin_rooms,
    admin_users,
    auth,
    dashboard,
    guests,
    health,
    hotel,
    reservations,
    rooms,
)

api_router: APIRouter = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(dashboard.router)
api_router.include_router(hotel.router)
api_router.include_router(reservations.router)
api_router.include_router(guests.router)
api_router.include_router(rooms.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_roles.router)
api_router.include_router(admin_audit.router)
api_router.include_router(admin_rooms.room_types)
api_router.include_router(admin_rooms.rooms)
