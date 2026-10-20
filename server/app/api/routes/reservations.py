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
    ReservationListQuery,
    ReservationNoShow,
    ReservationPage,
    ReservationUpdate,
    ReservationView,
    RoomAssignment,
    RoomUnassignment,
    WalkInCreate,
)
from app.services import reservation_list as list_service
from app.services import reservations as reservation_service
from app.services.availability import AvailabilityService
from app.services.reservations import ReservationService

router: APIRouter = APIRouter(prefix="/reservations", tags=["reservations"])

ReservationViewer = Annotated[User, Depends(require_permission(P.RESERVATION_VIEW))]
BookingUser = Annotated[User, Depends(require_permission(P.RESERVATION_CREATE))]
ModifyUser = Annotated[User, Depends(require_permission(P.RESERVATION_MODIFY))]
CancelUser = Annotated[User, Depends(require_permission(P.RESERVATION_CANCEL))]
WalkInUser = Annotated[
    User, Depends(require_permission(P.RESERVATION_CREATE, P.RESERVATION_CHECK_IN))
]
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


def _check_in_service(
    user: CheckInUser,
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationService:
    return _change_service(user, db, bus, property_id, client, today, permissions)


def _check_out_service(
    user: CheckOutUser,
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationService:
    return _change_service(user, db, bus, property_id, client, today, permissions)


def _walk_in_service(
    user: WalkInUser,
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
    permissions: CurrentPermissions,
) -> ReservationService:
    return _change_service(user, db, bus, property_id, client, today, permissions)


ChangeService = Annotated[ReservationService, Depends(_modify_service)]
CheckInService = Annotated[ReservationService, Depends(_check_in_service)]
CheckOutService = Annotated[ReservationService, Depends(_check_out_service)]
WalkInService = Annotated[ReservationService, Depends(_walk_in_service)]
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


@router.post(
    "/walk-in",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": NoAvailabilityResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def walk_in(
    body: WalkInCreate,
    idempotency_key: IdempotencyKeyHeader,
    response: Response,
    service: WalkInService,
) -> ReservationDetail:
    """Book from today (source walk_in) and check in to `room_id`, in one transaction.
    Needs reservation.create and reservation.check_in. Idempotent like POST /."""
    detail, replayed = await service.walk_in(body, idempotency_key)
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
        property_id,
        query.check_in,
        query.check_out,
        query.adults,
        query.children,
        query.exclude_reservation_id,
    )


@router.get("")
async def list_reservations(
    query: Annotated[ReservationListQuery, Query()],
    db: SessionDep,
    user: ReservationViewer,
    property_id: CurrentPropertyId,
    today: HotelTodayDep,
) -> ReservationPage:
    """Search (confirmation code or guest name), filter by status, dates or one of
    today's views, sort and page. Guest names only; no contact details."""
    return await list_service.list_reservations(db, query, property_id=property_id, today=today)


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


@router.post("/{reservation_id}/preview-change", responses=CHANGE_ERRORS)
async def preview_change(
    reservation_id: ReservationId, body: ReservationUpdate, service: ChangeService
) -> ReservationView:
    """What PATCH would make of the booking (nights, prices, total, room), with the same
    checks and errors, without saving anything. `history` is empty and `version` is the
    current one."""
    return await service.modify(reservation_id, body, dry_run=True)


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
    reservation_id: ReservationId, body: CheckInRequest, service: CheckInService
) -> DashboardRoom:
    """Check the guest in to `room_id` (ready, vacant, of the reserved type), from the
    arrival day on. Returns the room's updated dashboard entry."""
    return await service.check_in(reservation_id, body.room_id, body.room_version)


@router.post("/{reservation_id}/check-out", responses=ERRORS)
async def check_out(
    reservation_id: ReservationId, body: CheckOutRequest, service: CheckOutService
) -> DashboardRoom:
    """Check the guest out; the room becomes dirty. Leaving early shortens the stay to
    today and drops the unused nights. Returns the room's updated dashboard entry."""
    return await service.check_out(reservation_id, body.room_version)


@router.post("/{reservation_id}/assign-room", responses=CHANGE_ERRORS)
async def assign_room(
    reservation_id: ReservationId, body: RoomAssignment, service: CheckInService
) -> ReservationView:
    """Give a confirmed booking a room before arrival (active, of the reserved type, free
    on its nights), or move it to another one."""
    return await service.assign_room(reservation_id, body.version, body.room_id)


@router.post("/{reservation_id}/unassign-room", responses=CHANGE_ERRORS)
async def unassign_room(
    reservation_id: ReservationId, body: RoomUnassignment, service: CheckInService
) -> ReservationView:
    return await service.unassign_room(reservation_id, body.version)
