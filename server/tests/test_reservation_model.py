"""Database-level rules of the reservations data model (see "Reservations" in CLAUDE.md)."""

import re
import string
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.confirmation_codes import ALPHABET, LENGTH, PATTERN, new_confirmation_code
from app.core.permissions import DEFAULT_ROLES, P
from app.models import (
    Guest,
    IdempotencyKey,
    Property,
    Reservation,
    ReservationNight,
    ReservationStatus,
    Room,
    RoomType,
    User,
)
from app.services.permissions import get_default_property_id
from tests.factories import new_guest, new_reservation

TODAY: date = date(2026, 1, 15)
NOW: datetime = datetime(2026, 1, 15, 12, tzinfo=UTC)


@pytest.fixture
async def room(db_session: AsyncSession) -> Room:
    pid = await get_default_property_id(db_session)
    room = Room(
        property_id=pid,
        number="101",
        floor=1,
        room_type=RoomType(
            property_id=pid, code="STD", name="Std", max_occupancy=2, base_rate_minor=129_900
        ),
    )
    db_session.add(room)
    await db_session.flush()
    return room


@pytest.fixture
async def other_property(db_session: AsyncSession) -> Property:
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    return other


def stay(room: Room, **overrides: object) -> Reservation:
    reservation = new_reservation(
        room.room_type, check_in_date=TODAY, check_out_date=TODAY + timedelta(days=2)
    )
    for name, value in overrides.items():
        setattr(reservation, name, value)
    return reservation


async def assert_rejected(session: AsyncSession, obj: object, constraint: str) -> None:
    session.add(obj)
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()


# --- confirmation codes ------------------------------------------------------------------


def test_confirmation_code_pattern_matches_exactly_the_alphabet() -> None:
    allowed = {c for c in string.ascii_uppercase + string.digits if re.fullmatch(PATTERN, c * 8)}
    assert allowed == set(ALPHABET)
    assert not set(ALPHABET) & set("01OIL")


def test_new_confirmation_codes_are_random_and_well_formed() -> None:
    codes = {new_confirmation_code() for _ in range(200)}
    assert len(codes) == 200
    assert all(len(c) == LENGTH and re.fullmatch(PATTERN, c) for c in codes)


@pytest.mark.parametrize("code", ["ABC123", "abcd2345", "ABCD234O", "ABCD234"])
async def test_confirmation_code_format_enforced(
    db_session: AsyncSession, room: Room, code: str
) -> None:
    await assert_rejected(
        db_session, stay(room, confirmation_code=code), "ck_reservations_confirmation_code_format"
    )


# --- money -------------------------------------------------------------------------------


async def test_nights_and_total_are_stored(db_session: AsyncSession, room: Room) -> None:
    reservation = new_reservation(
        room.room_type,
        check_in_date=TODAY,
        check_out_date=TODAY + timedelta(days=3),
        rate_minor=129_900,
    )
    db_session.add(reservation)
    await db_session.flush()

    nights = (
        await db_session.scalars(
            select(ReservationNight)
            .where(ReservationNight.reservation_id == reservation.id)
            .order_by(ReservationNight.night_date)
        )
    ).all()
    assert [(n.night_date, n.rate_minor) for n in nights] == [
        (TODAY + timedelta(days=i), 129_900) for i in range(3)
    ]
    assert (reservation.total_minor, reservation.currency) == (389_700, "SEK")


async def test_one_row_per_night(db_session: AsyncSession, room: Room) -> None:
    reservation = stay(room)
    db_session.add(reservation)
    await db_session.flush()

    with pytest.raises(IntegrityError, match="pk_reservation_nights"):
        await db_session.execute(
            text(
                "INSERT INTO reservation_nights (reservation_id, night_date, rate_minor) "
                "VALUES (:id, :night, 1)"
            ),
            {"id": reservation.id, "night": TODAY},
        )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"total_minor": -1}, "ck_reservations_total_non_negative"),
        ({"currency": "sek"}, "ck_reservations_currency_iso_code"),
        ({"currency": "KR"}, "ck_reservations_currency_iso_code"),
    ],
)
async def test_money_checks(
    db_session: AsyncSession, room: Room, overrides: dict[str, object], constraint: str
) -> None:
    await assert_rejected(db_session, stay(room, **overrides), constraint)


async def test_rates_are_not_negative(db_session: AsyncSession, room: Room) -> None:
    reservation = stay(room)
    reservation.nights[0].rate_minor = -100
    await assert_rejected(db_session, reservation, "ck_reservation_nights_rate_non_negative")


async def test_base_rate_is_not_negative(db_session: AsyncSession, room: Room) -> None:
    await assert_rejected(
        db_session,
        RoomType(
            property_id=room.property_id,
            code="NEG",
            name="Negative",
            max_occupancy=1,
            base_rate_minor=-1,
        ),
        "ck_room_types_base_rate_non_negative",
    )


# --- status timestamps -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "overrides", "constraint"),
    [
        (ReservationStatus.CHECKED_IN, {"checked_in_at": None}, "checked_in_at_matches_status"),
        (ReservationStatus.CONFIRMED, {"checked_in_at": NOW}, "checked_in_at_matches_status"),
        (ReservationStatus.CHECKED_OUT, {"checked_out_at": None}, "checked_out_at_matches_status"),
        (ReservationStatus.CHECKED_IN, {"checked_out_at": NOW}, "checked_out_at_matches_status"),
        (ReservationStatus.CANCELLED, {"cancelled_at": None}, "cancelled_at_matches_status"),
        (ReservationStatus.NO_SHOW, {"cancelled_at": None}, "cancelled_at_matches_status"),
        (ReservationStatus.CONFIRMED, {"cancelled_at": NOW}, "cancelled_at_matches_status"),
        (
            ReservationStatus.CONFIRMED,
            {"cancellation_reason": "Changed plans"},
            "cancellation_details_need_cancelled_at",
        ),
    ],
)
async def test_status_timestamps_match_the_status(
    db_session: AsyncSession,
    room: Room,
    status: ReservationStatus,
    overrides: dict[str, object],
    constraint: str,
) -> None:
    reservation = new_reservation(
        room.room_type,
        check_in_date=TODAY,
        check_out_date=TODAY + timedelta(days=1),
        status=status,
        room=room,
    )
    for name, value in overrides.items():
        setattr(reservation, name, value)
    await assert_rejected(db_session, reservation, f"ck_reservations_{constraint}")


# --- one property ------------------------------------------------------------------------


async def test_room_type_must_belong_to_the_reservations_property(
    db_session: AsyncSession, room: Room, other_property: Property
) -> None:
    theirs = RoomType(property_id=other_property.id, code="XXX", name="Theirs", max_occupancy=2)
    db_session.add(theirs)
    await db_session.flush()

    await assert_rejected(
        db_session,
        new_reservation(
            theirs.id,
            property_id=room.property_id,
            check_in_date=TODAY,
            check_out_date=TODAY + timedelta(days=1),
        ),
        "fk_reservations_room_type_id_property_id_room_types",
    )


async def test_room_must_belong_to_the_reservations_property(
    db_session: AsyncSession, room: Room, other_property: Property
) -> None:
    theirs_type = RoomType(
        property_id=other_property.id, code="XXX", name="Theirs", max_occupancy=2
    )
    theirs = Room(property_id=other_property.id, number="101", floor=1, room_type=theirs_type)
    db_session.add(theirs)
    await db_session.flush()

    await assert_rejected(
        db_session,
        stay(room, room_id=theirs.id),
        "fk_reservations_room_id_property_id_rooms",
    )


async def test_guest_must_belong_to_the_reservations_property(
    db_session: AsyncSession, room: Room, other_property: Property
) -> None:
    await assert_rejected(
        db_session,
        stay(room, guest=new_guest(other_property.id)),
        "fk_reservations_guest_id_property_id_guests",
    )


# --- optimistic locking ------------------------------------------------------------------


async def test_reservation_update_bumps_version(db_session: AsyncSession, room: Room) -> None:
    reservation = stay(room)
    db_session.add(reservation)
    await db_session.flush()
    assert reservation.version == 1

    reservation.notes = "Late arrival"
    await db_session.flush()

    assert reservation.version == 2


async def test_stale_reservation_update_rejected(db_session: AsyncSession, room: Room) -> None:
    reservation = stay(room)
    db_session.add(reservation)
    await db_session.flush()
    await db_session.execute(
        text("UPDATE reservations SET version = version + 1 WHERE id = :id"),
        {"id": reservation.id},
    )

    reservation.notes = "Late arrival"
    with pytest.raises(StaleDataError):
        await db_session.flush()


# --- guests ------------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["Ada@Example.com", " ada@example.com", ""])
async def test_guest_email_stored_normalised(
    db_session: AsyncSession, room: Room, email: str
) -> None:
    await assert_rejected(
        db_session, new_guest(room.property_id, email=email), "ck_guests_email_normalised"
    )


async def test_guest_search_columns_are_generated(db_session: AsyncSession, room: Room) -> None:
    guest = new_guest(
        room.property_id, "Åsa", "Lindqvist", email="asa@example.com", phone="+46 70-123 45 67"
    )
    db_session.add(guest)
    await db_session.flush()
    await db_session.refresh(guest)

    assert guest.phone_digits == "46701234567"
    assert guest.search_text == "åsa lindqvist asa@example.com 46701234567"
    assert guest.created_at is not None and guest.updated_at is not None

    found = await db_session.scalar(
        select(Guest.id).where(
            Guest.property_id == room.property_id, Guest.search_text.ilike("%lindq%")
        )
    )
    assert found == guest.id


async def test_guest_without_contact_details(db_session: AsyncSession, room: Room) -> None:
    guest = new_guest(room.property_id, "Bo", "Berg")
    db_session.add(guest)
    await db_session.flush()
    await db_session.refresh(guest)

    assert guest.phone_digits is None
    assert guest.search_text == "bo berg  "


# --- idempotency keys --------------------------------------------------------------------


def idempotency_key(user: User, key: str = "key-1") -> IdempotencyKey:
    return IdempotencyKey(
        key=key,
        user_id=user.id,
        endpoint="POST /api/reservations",
        request_hash="0" * 64,
        response_reference="reservation:1",
    )


async def test_idempotency_key_unique_per_user(db_session: AsyncSession) -> None:
    alice = User(username="alice", password_hash="x", full_name="Alice")
    bob = User(username="bob", password_hash="x", full_name="Bob")
    db_session.add_all([alice, bob])
    await db_session.flush()
    db_session.add_all([idempotency_key(alice), idempotency_key(bob)])
    await db_session.flush()  # the same key from another user is fine

    await assert_rejected(db_session, idempotency_key(alice), "uq_idempotency_keys_key_user_id")


# --- properties and permissions ----------------------------------------------------------


async def test_property_booking_defaults(db_session: AsyncSession) -> None:
    prop = Property(name="New Hotel", time_zone="UTC")
    db_session.add(prop)
    await db_session.flush()
    await db_session.refresh(prop)

    assert (prop.currency, prop.max_stay_nights, prop.booking_horizon_days) == ("SEK", 30, 730)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"currency": "sek"}, "ck_properties_currency_iso_code"),
        ({"max_stay_nights": 0}, "ck_properties_max_stay_nights_range"),
        ({"booking_horizon_days": 0}, "ck_properties_booking_horizon_days_range"),
    ],
)
async def test_property_booking_checks(
    db_session: AsyncSession, overrides: dict[str, object], constraint: str
) -> None:
    prop = Property(name="New Hotel", time_zone="UTC")
    for name, value in overrides.items():
        setattr(prop, name, value)
    await assert_rejected(db_session, prop, constraint)


def test_override_rate_is_for_managers_only() -> None:
    holders = {r.name for r in DEFAULT_ROLES if P.RESERVATION_OVERRIDE_RATE in r.permissions}
    assert holders == {"Admin", "General manager", "Front office manager"}
