from fastapi import APIRouter, Depends

from app.api.deps import CurrentPropertyId, HotelTodayDep, require_permission
from app.core.permissions import P
from app.db.session import SessionDep
from app.schemas.dashboard import DashboardRooms, DashboardSummary
from app.services import dashboard as dashboard_service

router: APIRouter = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_permission(P.DASHBOARD_VIEW))],
)


@router.get("/rooms")
async def rooms(
    db: SessionDep, today: HotelTodayDep, property_id: CurrentPropertyId
) -> DashboardRooms:
    return await dashboard_service.get_dashboard_rooms(db, today, property_id=property_id)


@router.get("/summary")
async def summary(
    db: SessionDep, today: HotelTodayDep, property_id: CurrentPropertyId
) -> DashboardSummary:
    return await dashboard_service.get_dashboard_summary(db, today, property_id)
