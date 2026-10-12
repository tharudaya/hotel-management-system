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
from app.schemas.actions import CheckInRequest, CheckOutRequest, ErrorResponse
from app.schemas.dashboard import DashboardRoom
from app.schemas.reservations import ReservationDetail
from app.services import reservations as reservation_service

router: APIRouter = APIRouter(prefix="/reservations", tags=["reservations"])

ReservationViewer = Annotated[User, Depends(require_permission(P.RESERVATION_VIEW))]
CheckInUser = Annotated[User, Depends(require_permission(P.RESERVATION_CHECK_IN))]
CheckOutUser = Annotated[User, Depends(require_permission(P.RESERVATION_CHECK_OUT))]
ReservationId = Annotated[int, Path(gt=0)]
ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
}


@router.get("/{reservation_id}", responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}})
async def get_reservation(
    reservation_id: ReservationId,
    db: SessionDep,
    user: ReservationViewer,
    permissions: CurrentPermissions,
) -> ReservationDetail:
    """Guest email and phone are included only with guest.view_contact."""
    return await reservation_service.get_reservation_detail(
        db, reservation_id, include_contact=P.GUEST_VIEW_CONTACT in permissions
    )


@router.post("/{reservation_id}/check-in", responses=ERRORS)
async def check_in(
    reservation_id: ReservationId,
    body: CheckInRequest,
    db: SessionDep,
    bus: EventBusDep,
    user: CheckInUser,
    client: ClientInfoDep,
    today: HotelTodayDep,
) -> DashboardRoom:
    """Check the guest in to `room_id`. Returns the room's updated dashboard entry."""
    return await reservation_service.check_in(
        db,
        bus,
        reservation_id=reservation_id,
        room_id=body.room_id,
        expected_room_version=body.room_version,
        actor=user,
        client=client,
        today=today,
    )


@router.post("/{reservation_id}/check-out", responses=ERRORS)
async def check_out(
    reservation_id: ReservationId,
    body: CheckOutRequest,
    db: SessionDep,
    bus: EventBusDep,
    user: CheckOutUser,
    client: ClientInfoDep,
    today: HotelTodayDep,
) -> DashboardRoom:
    """Check the guest out; the room becomes dirty. Returns its updated dashboard entry."""
    return await reservation_service.check_out(
        db,
        bus,
        reservation_id=reservation_id,
        expected_room_version=body.room_version,
        actor=user,
        client=client,
        today=today,
    )
