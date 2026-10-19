"""Builders for guests and reservations in tests (not added to a session).

They fill in what every reservation needs (property, source, nights with rates, total,
currency, a valid confirmation code, status timestamps), so a test states only what
matters to it.
"""

from datetime import UTC, date, datetime, timedelta

from app.core.confirmation_codes import new_confirmation_code
from app.models import (
    Guest,
    Reservation,
    ReservationNight,
    ReservationSource,
    ReservationStatus,
    Room,
    RoomType,
)

DEFAULT_RATE_MINOR: int = 100_000


def new_guest(
    property_id: int,
    first_name: str = "Ada",
    last_name: str = "Guest",
    *,
    email: str | None = None,
    phone: str | None = None,
) -> Guest:
    return Guest(
        property_id=property_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
    )


def new_reservation(
    room_type: RoomType | int,
    *,
    check_in_date: date,
    check_out_date: date,
    status: ReservationStatus = ReservationStatus.CONFIRMED,
    property_id: int | None = None,
    room: Room | int | None = None,
    guest: Guest | None = None,
    first_name: str = "Ada",
    confirmation_code: str | None = None,
    rate_minor: int = DEFAULT_RATE_MINOR,
    adults: int = 1,
    children: int = 0,
    is_vip: bool = False,
    notes: str | None = None,
) -> Reservation:
    """A reservation of `room_type` (an object, or an id together with `property_id`)."""
    if isinstance(room_type, RoomType):
        property_id = room_type.property_id
    assert property_id is not None, "pass property_id with a room type id"
    nights = [
        check_in_date + timedelta(days=i) for i in range((check_out_date - check_in_date).days)
    ]
    now = datetime.now(UTC)
    in_house_or_left = status in (ReservationStatus.CHECKED_IN, ReservationStatus.CHECKED_OUT)
    reservation = Reservation(
        property_id=property_id,
        confirmation_code=confirmation_code or new_confirmation_code(),
        guest=guest or new_guest(property_id, first_name),
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        status=status,
        source=ReservationSource.PHONE,
        adults=adults,
        children=children,
        is_vip=is_vip,
        notes=notes,
        nights=[ReservationNight(night_date=night, rate_minor=rate_minor) for night in nights],
        total_minor=rate_minor * len(nights),
        currency="SEK",
        checked_in_at=now if in_house_or_left else None,
        checked_out_at=now if status is ReservationStatus.CHECKED_OUT else None,
        cancelled_at=(
            now if status in (ReservationStatus.CANCELLED, ReservationStatus.NO_SHOW) else None
        ),
    )
    if isinstance(room_type, RoomType):
        reservation.room_type = room_type
    else:
        reservation.room_type_id = room_type
    if isinstance(room, Room):
        reservation.room = room
    elif room is not None:
        reservation.room_id = room
    return reservation
