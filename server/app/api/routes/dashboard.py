from fastapi import APIRouter, Depends

from app.api.deps import HotelTodayDep, require_role
from app.db.session import SessionDep
from app.models import UserRole
from app.schemas.dashboard import DashboardRooms, DashboardSummary
from app.services import dashboard as dashboard_service

router: APIRouter = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_role(UserRole.FRONT_DESK, UserRole.MANAGER, UserRole.ADMIN))],
)


@router.get("/rooms")
async def rooms(db: SessionDep, today: HotelTodayDep) -> DashboardRooms:
    return await dashboard_service.get_dashboard_rooms(db, today)


@router.get("/summary")
async def summary(db: SessionDep, today: HotelTodayDep) -> DashboardSummary:
    return await dashboard_service.get_dashboard_summary(db, today)
