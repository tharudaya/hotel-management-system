from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import (
    ClientInfoDep,
    CurrentPermissions,
    EventBusDep,
    HotelTodayDep,
    require_permission,
)
from app.core.permissions import P
from app.db.session import SessionDep
from app.models import User
from app.schemas.actions import ErrorResponse, HousekeepingStatusUpdate
from app.schemas.dashboard import DashboardRoom
from app.services import rooms as room_service

router: APIRouter = APIRouter(prefix="/rooms", tags=["rooms"])

HousekeepingUser = Annotated[User, Depends(require_permission(P.ROOM_UPDATE_HOUSEKEEPING_STATUS))]


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
    permissions: CurrentPermissions,
    client: ClientInfoDep,
    today: HotelTodayDep,
) -> DashboardRoom:
    """Change housekeeping status (allowed transitions only). Returns the updated entry.

    Moving a room into or out of "out of order" also needs room.set_out_of_order.
    """
    return await room_service.change_housekeeping_status(
        db,
        bus,
        room_id=room_id,
        status=body.status,
        expected_version=body.version,
        actor=user,
        actor_permissions=permissions,
        client=client,
        today=today,
    )
