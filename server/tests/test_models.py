"""Database-level rules on the core tables (constraints, optimistic locking)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.models import (
    Guest,
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
    User,
)
from app.services.permissions import get_default_property_id

TODAY: date = date(2026, 1, 15)


@pytest.fixture
async def room(db_session: AsyncSession) -> Room:
    pid = await get_default_property_id(db_session)
    room = Room(
        property_id=pid,
        number="101",
        floor=1,
        room_type=RoomType(property_id=pid, code="STD", name="Std", max_occupancy=2),
    )
    db_session.add(room)
    await db_session.flush()
    return room


def make_reservation(
    room: Room | None,
    *,
    code: str,
    check_in: int = 0,
    check_out: int = 2,
    status: ReservationStatus = ReservationStatus.CONFIRMED,
    room_type: RoomType | None = None,
) -> Reservation:
    resolved_type = room_type if room_type is not None else room.room_type if room else None
    assert resolved_type is not None
    return Reservation(
        confirmation_code=code,
        guest=Guest(first_name="Test", last_name="Guest"),
        room_type=resolved_type,
        room=room,
        check_in_date=TODAY + timedelta(days=check_in),
        check_out_date=TODAY + timedelta(days=check_out),
        status=status,
    )


async def assert_rejected(session: AsyncSession, obj: object, constraint: str) -> None:
    session.add(obj)
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()
    await session.rollback()


async def test_defaults(db_session: AsyncSession, room: Room) -> None:
    reservation = make_reservation(room, code="A1")
    db_session.add(reservation)
    await db_session.flush()
    await db_session.refresh(reservation)
    await db_session.refresh(room)

    assert room.housekeeping_status is HousekeepingStatus.DIRTY
    assert room.version == 1
    assert reservation.status is ReservationStatus.CONFIRMED
    assert (reservation.adults, reservation.children, reservation.is_vip) == (1, 0, False)
    assert reservation.created_at is not None


async def test_check_out_must_be_after_check_in(db_session: AsyncSession, room: Room) -> None:
    await assert_rejected(
        db_session,
        make_reservation(room, code="A1", check_in=2, check_out=2),
        "ck_reservations_check_out_after_check_in",
    )


async def test_overlapping_active_reservations_on_same_room_rejected(
    db_session: AsyncSession, room: Room
) -> None:
    db_session.add(make_reservation(room, code="A1", check_in=0, check_out=3))
    await db_session.flush()

    await assert_rejected(
        db_session,
        make_reservation(room, code="A2", check_in=2, check_out=4),
        "ex_reservations_room_no_overlap",
    )


async def test_back_to_back_and_inactive_reservations_may_share_room(
    db_session: AsyncSession, room: Room
) -> None:
    db_session.add_all(
        [
            make_reservation(room, code="A1", check_in=0, check_out=3),
            # Arrives the day the first one leaves: [0, 3) and [3, 5) don't overlap.
            make_reservation(room, code="A2", check_in=3, check_out=5),
            # Cancelled stays don't hold the room.
            make_reservation(
                room, code="A3", check_in=1, check_out=2, status=ReservationStatus.CANCELLED
            ),
        ]
    )
    await db_session.flush()


async def test_checked_in_requires_room(db_session: AsyncSession, room: Room) -> None:
    await assert_rejected(
        db_session,
        make_reservation(
            None, code="A1", status=ReservationStatus.CHECKED_IN, room_type=room.room_type
        ),
        "ck_reservations_in_house_has_room",
    )


async def test_only_one_party_in_house_per_room(db_session: AsyncSession, room: Room) -> None:
    # An overstay: the first party's dates have passed but they are still in house.
    db_session.add(
        make_reservation(
            room, code="A1", check_in=-3, check_out=-1, status=ReservationStatus.CHECKED_IN
        )
    )
    await db_session.flush()

    await assert_rejected(
        db_session,
        make_reservation(room, code="A2", status=ReservationStatus.CHECKED_IN),
        "uq_reservations_room_in_house",
    )


async def test_confirmation_code_unique(db_session: AsyncSession, room: Room) -> None:
    db_session.add(make_reservation(room, code="A1", check_in=0, check_out=1))
    await db_session.flush()

    await assert_rejected(
        db_session,
        make_reservation(room, code="A1", check_in=5, check_out=6),
        "uq_reservations_confirmation_code",
    )


async def test_room_number_unique_per_property(db_session: AsyncSession, room: Room) -> None:
    await assert_rejected(
        db_session,
        Room(property_id=room.property_id, number="101", floor=1, room_type=room.room_type),
        "uq_rooms_property_id_number",
    )


async def test_same_room_number_at_another_property(db_session: AsyncSession, room: Room) -> None:
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    std = RoomType(property_id=other.id, code="STD", name="Std", max_occupancy=2)
    db_session.add(Room(property_id=other.id, number="101", floor=1, room_type=std))

    await db_session.flush()  # no conflict with room 101 of the first property


@pytest.mark.parametrize(
    ("code", "name", "constraint"),
    [
        ("STD", "Another", "uq_room_types_property_id_code"),
        ("OTH", "Std", "uq_room_types_property_id_name"),
    ],
)
async def test_room_type_code_and_name_unique_per_property(
    db_session: AsyncSession, room: Room, code: str, name: str, constraint: str
) -> None:
    await assert_rejected(
        db_session,
        RoomType(property_id=room.property_id, code=code, name=name, max_occupancy=2),
        constraint,
    )


async def test_room_cannot_use_another_propertys_room_type(
    db_session: AsyncSession, room: Room
) -> None:
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    theirs = RoomType(property_id=other.id, code="XXX", name="Theirs", max_occupancy=2)
    db_session.add(theirs)
    await db_session.flush()

    await assert_rejected(
        db_session,
        Room(property_id=room.property_id, number="999", floor=9, room_type_id=theirs.id),
        "fk_rooms_room_type_id_property_id_room_types",
    )


@pytest.mark.parametrize(("is_active", "retired"), [(False, False), (True, True)])
async def test_retired_flag_and_date_must_agree(
    db_session: AsyncSession, room: Room, is_active: bool, retired: bool
) -> None:
    await assert_rejected(
        db_session,
        Room(
            property_id=room.property_id,
            number="998",
            floor=9,
            room_type=room.room_type,
            is_active=is_active,
            retired_at=datetime(2026, 1, 1, tzinfo=UTC) if retired else None,
        ),
        "ck_rooms_retired_consistent",
    )


async def test_new_rooms_and_types_are_active(db_session: AsyncSession, room: Room) -> None:
    room_type = await db_session.get(RoomType, room.room_type_id)
    assert room_type is not None
    await db_session.refresh(room)
    await db_session.refresh(room_type)
    assert room.is_active and room.retired_at is None and room.created_at is not None
    assert room_type.is_active and room_type.created_at is not None


async def test_username_must_be_lowercase(db_session: AsyncSession) -> None:
    await assert_rejected(
        db_session,
        User(username="Alice", password_hash="x", full_name="Alice"),
        "ck_users_username_lowercase",
    )


async def test_room_update_bumps_version(db_session: AsyncSession, room: Room) -> None:
    room.housekeeping_status = HousekeepingStatus.CLEAN
    await db_session.flush()

    assert room.version == 2


async def test_stale_room_update_rejected(db_session: AsyncSession, room: Room) -> None:
    # Someone else updates the row after we loaded it (raw SQL bypasses our identity map).
    await db_session.execute(
        text("UPDATE rooms SET version = version + 1 WHERE id = :id"), {"id": room.id}
    )

    room.housekeeping_status = HousekeepingStatus.CLEAN
    with pytest.raises(StaleDataError):
        await db_session.flush()
