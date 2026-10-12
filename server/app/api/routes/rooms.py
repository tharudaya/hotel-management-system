from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import ClientInfoDep, EventBusDep, HotelTodayDep, require_role
from app.db.session import SessionDep
from app.models import User, UserRole
from app.schemas.actions import ErrorResponse, HousekeepingStatusUpdate
from app.schemas.dashboard import DashboardRoom
from app.services import rooms as room_service

router: APIRouter = APIRouter(prefix="/rooms", tags=["rooms"])

HousekeepingUser = Annotated[
    User, Depends(require_role(UserRole.HOUSEKEEPING, UserRole.MANAGER, UserRole.ADMIN))
]


@router.patch(
    "/{room_id}/housekeeping-status",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def update_housekeeping_status(
    room_id: Annotated[int, Path(gt=0)],
    body: HousekeepingStatusUpdate,
    db: SessionDep,
    bus: EventBusDep,
    user: HousekeepingUser,
    client: ClientInfoDep,
    today: HotelTodayDep,
) -> DashboardRoom:
    """Change housekeeping status (allowed transitions only). Returns the updated entry."""
    return await room_service.change_housekeeping_status(
        db,
        bus,
        room_id=room_id,
        status=body.status,
        expected_version=body.version,
        actor=user,
        client=client,
        today=today,
    )
