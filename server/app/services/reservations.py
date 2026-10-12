"""Reservation details, check-in and check-out."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditAction,
    Guest,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
    User,
)
from app.realtime.bus import EventBus
from app.schemas.dashboard import DashboardRoom
from app.schemas.reservations import GuestOut, ReservationDetail
from app.services.audit import ClientInfo, record_change
from app.services.errors import DomainError, InvalidTransitionError, NotFoundError
from app.services.room_changes import (
    commit,
    ensure_version,
    load_room,
    publish_room_changes,
    touch,
)
from app.services.rooms import HOUSEKEEPING_PHRASES

# How a reservation's status reads in messages shown to staff.
STATUS_PHRASES: dict[ReservationStatus, str] = {
    ReservationStatus.CONFIRMED: "not checked in yet",
    ReservationStatus.CHECKED_IN: "already checked in",
    ReservationStatus.CHECKED_OUT: "already checked out",
    ReservationStatus.CANCELLED: "cancelled",
    ReservationStatus.NO_SHOW: "marked as a no-show",
}

READY_FOR_GUEST: frozenset[HousekeepingStatus] = frozenset(
    {HousekeepingStatus.CLEAN, HousekeepingStatus.INSPECTED}
)


async def get_reservation_detail(
    db: AsyncSession, reservation_id: int, *, include_contact: bool
) -> ReservationDetail:
    row = (
        await db.execute(
            select(Reservation, Guest, RoomType, Room)
            .join(Reservation.guest)
            .join(Reservation.room_type)
            .outerjoin(Reservation.room)
            .where(Reservation.id == reservation_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"Reservation {reservation_id} not found")
    reservation, guest, room_type, room = row
    return ReservationDetail(
        id=reservation.id,
        confirmation_code=reservation.confirmation_code,
        status=reservation.status,
        check_in_date=reservation.check_in_date,
        check_out_date=reservation.check_out_date,
        nights=(reservation.check_out_date - reservation.check_in_date).days,
        adults=reservation.adults,
        children=reservation.children,
        is_vip=reservation.is_vip,
        notes=reservation.notes,
        room_type=room_type.code,
        room_type_name=room_type.name,
        room_id=room.id if room else None,
        room_number=room.number if room else None,
        guest=GuestOut(
            first_name=guest.first_name,
            last_name=guest.last_name,
            # Contact details need guest.view_contact (decided by the caller).
            email=guest.email if include_contact else None,
            phone=guest.phone if include_contact else None,
        ),
    )


async def _load_reservation_for_update(db: AsyncSession, reservation_id: int) -> Reservation:
    # Row lock: concurrent check-ins/outs of the same reservation run one after
    # the other, and the second sees the first one's result.
    reservation = await db.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reservation is None:
        raise NotFoundError(f"Reservation {reservation_id} not found")
    return reservation


async def _ensure_room_free_for(
    db: AsyncSession, room: Room, reservation: Reservation, today: date
) -> None:
    occupied = await db.scalar(
        select(Reservation.id).where(
            Reservation.room_id == room.id, Reservation.status == ReservationStatus.CHECKED_IN
        )
    )
    if occupied is not None:
        raise DomainError(f"Room {room.number} is occupied.", code="room_not_vacant")

    held_by_other = await db.scalar(
        select(Reservation.id).where(
            Reservation.room_id == room.id,
            Reservation.status == ReservationStatus.CONFIRMED,
            Reservation.id != reservation.id,
            Reservation.check_in_date < reservation.check_out_date,
            Reservation.check_out_date > today,
        )
    )
    if held_by_other is not None:
        raise DomainError(
            f"Room {room.number} is assigned to another reservation for these nights.",
            code="room_not_vacant",
        )


async def check_in(
    db: AsyncSession,
    bus: EventBus,
    *,
    reservation_id: int,
    room_id: int,
    expected_room_version: int,
    actor: User,
    property_id: int,
    client: ClientInfo,
    today: date,
) -> DashboardRoom:
    reservation = await _load_reservation_for_update(db, reservation_id)
    if reservation.status is not ReservationStatus.CONFIRMED:
        raise InvalidTransitionError(
            f"Cannot check in: the reservation is {STATUS_PHRASES[reservation.status]}."
        )
    if reservation.check_in_date > today:
        raise DomainError(
            f"Reservation arrives on {reservation.check_in_date.isoformat()}; "
            "it cannot be checked in before then.",
            code="too_early",
        )
    if reservation.check_out_date <= today:
        raise DomainError("Reservation's stay has already ended.", code="stay_ended")

    room = await load_room(db, room_id)
    ensure_version(room, expected_room_version)
    if room.room_type_id != reservation.room_type_id:
        raise DomainError(
            f"Room {room.number} is not of the reserved room type.", code="room_type_mismatch"
        )
    if room.housekeeping_status not in READY_FOR_GUEST:
        raise DomainError(
            f"Room {room.number} is {HOUSEKEEPING_PHRASES[room.housekeeping_status]}; "
            "it must be cleaned first (awaiting inspection or ready).",
            code="room_not_ready",
        )
    await _ensure_room_free_for(db, room, reservation, today)

    previous_room_id = reservation.room_id
    room_number = room.number  # read now: a failed commit expires loaded objects
    reservation.status = ReservationStatus.CHECKED_IN
    reservation.room_id = room.id
    touch(room)
    record_change(
        db,
        action=AuditAction.RESERVATION_CHECKED_IN,
        target_type="reservation",
        target_id=reservation.id,
        actor=actor,
        property_id=property_id,
        client=client,
        before={"status": ReservationStatus.CONFIRMED.value, "room_id": previous_room_id},
        after={
            "status": ReservationStatus.CHECKED_IN.value,
            "room_id": room.id,
            "room_number": room.number,
            "confirmation_code": reservation.confirmation_code,
        },
    )
    try:
        await commit(db, room)
    except IntegrityError:
        # Database backstop for the vacancy checks above (a concurrent check-in
        # or assignment won the race).
        await db.rollback()
        raise DomainError(
            f"Room {room_number} was just taken by another reservation.", code="room_not_vacant"
        ) from None

    # Moving the guest from a pre-assigned room changes that room's entry too.
    affected = {room_id}
    if previous_room_id is not None:
        affected.add(previous_room_id)
    entries = await publish_room_changes(db, bus, today, affected)
    return entries[room_id]


async def check_out(
    db: AsyncSession,
    bus: EventBus,
    *,
    reservation_id: int,
    expected_room_version: int,
    actor: User,
    property_id: int,
    client: ClientInfo,
    today: date,
) -> DashboardRoom:
    reservation = await _load_reservation_for_update(db, reservation_id)
    if reservation.status is not ReservationStatus.CHECKED_IN or reservation.room_id is None:
        raise InvalidTransitionError(
            f"Cannot check out: the reservation is {STATUS_PHRASES[reservation.status]}."
        )

    room = await load_room(db, reservation.room_id)
    ensure_version(room, expected_room_version)
    housekeeping_before = room.housekeeping_status

    reservation.status = ReservationStatus.CHECKED_OUT
    room.housekeeping_status = HousekeepingStatus.DIRTY
    touch(room)  # bumps the version even if the room was already dirty
    record_change(
        db,
        action=AuditAction.RESERVATION_CHECKED_OUT,
        target_type="reservation",
        target_id=reservation.id,
        actor=actor,
        property_id=property_id,
        client=client,
        before={
            "status": ReservationStatus.CHECKED_IN.value,
            "housekeeping_status": housekeeping_before.value,
        },
        after={
            "status": ReservationStatus.CHECKED_OUT.value,
            "housekeeping_status": HousekeepingStatus.DIRTY.value,
            "room_id": room.id,
            "room_number": room.number,
            "confirmation_code": reservation.confirmation_code,
        },
    )
    await commit(db, room)

    entries = await publish_room_changes(db, bus, today, [room.id])
    return entries[room.id]
