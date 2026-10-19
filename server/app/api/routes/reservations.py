from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status

from app.api.deps import (
    ClientInfoDep,
    CurrentPermissions,
    CurrentPropertyId,
    EventBusDep,
    HotelTodayDep,
    require_permission,
)
from app.core.permissions import P
from app.db.session import SessionDep
from app.models import User
from app.schemas.actions import (
    CheckInRequest,
    CheckOutRequest,
    ErrorResponse,
    NoAvailabilityResponse,
)
from app.schemas.availability import Availability, AvailabilityQuery
from app.schemas.dashboard import DashboardRoom
from app.schemas.reservations import (
    ReservationCancel,
    ReservationCreate,
    ReservationDetail,
    ReservationNoShow,
    ReservationUpdate,
    ReservationView,
)
from app.services import reservations as reservation_service
from app.services.availability import AvailabilityService
from app.services.reservations import ReservationService

router: APIRouter = APIRouter(prefix="/reservations", tags=["reservations"])

ReservationViewer = Annotated[User, Depends(require_permission(P.RESERVATION_VIEW))]
BookingUser = Annotated[User, Depends(require_permission(P.RESERVATION_CREATE))]
ModifyUser = Annotated[User, Depends(require_permission(P.RESERVATION_MODIFY))]
CancelUser = Annotated[User, Depends(require_permission(P.RESERVATION_CANCEL))]
CheckInUser = Annotated[User, Depends(require_permission(P.RESERVATION_CHECK_IN))]
CheckOutUser = Annotated[User, Depends(require_permission(P.RESERVATION_CHECK_OUT))]
ReservationId = Annotated[int, Path(gt=0)]
ConfirmationCode = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{8}$")]
# Random per booking attempt (e.g. a UUID), reused only to retry that same attempt.
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
]


def _change_service(
    user: User,
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationService:
    return ReservationService(db, bus, user, property_id, client, today, permissions)


def _modify_service(
    user: ModifyUser,
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationService:
    return _change_service(user, db, bus, property_id, client, today, permissions)


def _cancel_service(
    user: CancelUser,
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationService:
    return _change_service(user, db, bus, property_id, client, today, permissions)


ChangeService = Annotated[ReservationService, Depends(_modify_service)]
CancelService = Annotated[ReservationService, Depends(_cancel_service)]
CHANGE_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": NoAvailabilityResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}
ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": NoAvailabilityResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def create_reservation(
    body: ReservationCreate,
    idempotency_key: IdempotencyKeyHeader,
    response: Response,
    db: SessionDep,
    bus: EventBusDep,
    user: BookingUser,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationDetail:
    """Book a stay (status confirmed, no room yet) for an existing or a new guest.

    Retrying with the same Idempotency-Key and body returns the original reservation
    (header Idempotent-Replayed: true) instead of booking again; the same key with a
    different body is 422 idempotency_key_reused. 409 no_availability lists the full
    nights in unavailable_nights. A nightly_rate_minor other than the base rate needs
    reservation.override_rate (403 rate_override_not_allowed).
    """
    detail, replayed = await ReservationService(
        db, bus, user, property_id, client, today, permissions
    ).create(body, idempotency_key)
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return detail


# Declared before /{reservation_id}, which would otherwise match "availability".
@router.get(
    "/availability", responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}}
)
async def get_availability(
    query: Annotated[AvailabilityQuery, Query()],
    db: SessionDep,
    user: BookingUser,
    property_id: CurrentPropertyId,
    today: HotelTodayDep,
) -> Availability:
    """Rooms free on every night of the stay, per room type that fits the party, with the
    default nightly rates. 422 for a stay the hotel does not take (past, too long, too far
    ahead)."""
    return await AvailabilityService(db, today).available_by_room_type(
        property_id, query.check_in, query.check_out, query.adults, query.children
    )


@router.get(
    "/by-code/{confirmation_code}",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def get_reservation_by_code(
    confirmation_code: ConfirmationCode,
    db: SessionDep,
    user: ReservationViewer,
    property_id: CurrentPropertyId,
    permissions: CurrentPermissions,
) -> ReservationView:
    """Look a booking up by the code the guest reads out (case does not matter)."""
    reservation_id = await reservation_service.find_reservation_id_by_code(
        db, confirmation_code, property_id
    )
    return await reservation_service.get_reservation_view(
        db,
        reservation_id,
        include_contact=P.GUEST_VIEW_CONTACT in permissions,
        property_id=property_id,
    )


@router.get("/{reservation_id}", responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}})
async def get_reservation(
    reservation_id: ReservationId,
    db: SessionDep,
    user: ReservationViewer,
    property_id: CurrentPropertyId,
    permissions: CurrentPermissions,
) -> ReservationView:
    """Details with nights, guest and history (from the audit log). Guest email and phone
    are included only with guest.view_contact."""
    return await reservation_service.get_reservation_view(
        db,
        reservation_id,
        include_contact=P.GUEST_VIEW_CONTACT in permissions,
        property_id=property_id,
    )


@router.patch("/{reservation_id}", responses=CHANGE_ERRORS)
async def modify_reservation(
    reservation_id: ReservationId, body: ReservationUpdate, service: ChangeService
) -> ReservationView:
    """Change dates, room type, guests, notes, VIP or price (send `version`).

    Confirmed bookings only, except that a checked-in guest's departure date and notes
    can change. On 409 no_availability the booking is unchanged. A price change needs
    reservation.override_rate.
    """
    return await service.modify(reservation_id, body)


@router.post("/{reservation_id}/cancel", responses=CHANGE_ERRORS)
async def cancel_reservation(
    reservation_id: ReservationId, body: ReservationCancel, service: CancelService
) -> ReservationView:
    """confirmed -> cancelled, with a reason. A room assigned in advance is released."""
    return await service.cancel(reservation_id, body)


@router.post("/{reservation_id}/no-show", responses=CHANGE_ERRORS)
async def mark_no_show(
    reservation_id: ReservationId, body: ReservationNoShow, service: CancelService
) -> ReservationView:
    """confirmed -> no_show, on or after the arrival day."""
    return await service.mark_no_show(reservation_id, body)


@router.post("/{reservation_id}/check-in", responses=ERRORS)
async def check_in(
    reservation_id: ReservationId,
    body: CheckInRequest,
    db: SessionDep,
    bus: EventBusDep,
    user: CheckInUser,
    property_id: CurrentPropertyId,
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
        property_id=property_id,
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
    property_id: CurrentPropertyId,
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
        property_id=property_id,
        client=client,
        today=today,
    )
