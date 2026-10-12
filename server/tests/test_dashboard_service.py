"""Unit tests for the occupancy derivation (no database)."""

from datetime import date, timedelta

from app.models import HousekeepingStatus
from app.schemas.dashboard import OccupancyStatus
from app.services.dashboard import Stay, derive_occupancy

TODAY: date = date(2026, 3, 10)
HK = HousekeepingStatus


def stay(check_in: int, check_out: int, *, reservation_id: int = 1, name: str = "Ann") -> Stay:
    """Day offsets are relative to TODAY."""
    return Stay(
        reservation_id=reservation_id,
        guest_name=name,
        check_in_date=TODAY + timedelta(days=check_in),
        check_out_date=TODAY + timedelta(days=check_out),
        is_vip=False,
        has_notes=False,
    )


def test_vacant() -> None:
    result = derive_occupancy(HK.INSPECTED, None, None, TODAY)

    assert result.status is OccupancyStatus.VACANT
    assert (result.stay, result.nights_remaining, result.has_arrival_today) == (None, None, False)


def test_occupied_counts_nights_remaining() -> None:
    guest = stay(-2, 3)

    result = derive_occupancy(HK.DIRTY, guest, None, TODAY)

    assert result.status is OccupancyStatus.OCCUPIED
    assert result.stay == guest
    assert result.nights_remaining == 3


def test_departing_today() -> None:
    result = derive_occupancy(HK.CLEAN, stay(-2, 0), None, TODAY)

    assert result.status is OccupancyStatus.DEPARTING_TODAY
    assert result.nights_remaining == 0


def test_overstay_is_departing_with_zero_nights() -> None:
    result = derive_occupancy(HK.CLEAN, stay(-4, -1), None, TODAY)

    assert result.status is OccupancyStatus.DEPARTING_TODAY
    assert result.nights_remaining == 0


def test_arriving_today_shows_length_of_stay() -> None:
    guest = stay(0, 4)

    result = derive_occupancy(HK.INSPECTED, None, guest, TODAY)

    assert result.status is OccupancyStatus.ARRIVING_TODAY
    assert result.stay == guest
    assert result.nights_remaining == 4
    assert result.has_arrival_today


def test_future_arrival_leaves_room_vacant() -> None:
    result = derive_occupancy(HK.INSPECTED, None, stay(2, 4), TODAY)

    assert result.status is OccupancyStatus.VACANT
    assert not result.has_arrival_today


def test_departure_and_arrival_same_day_shows_departing_guest_and_flags_arrival() -> None:
    leaving = stay(-3, 0, reservation_id=1, name="Leaving")
    arriving = stay(0, 2, reservation_id=2, name="Arriving")

    result = derive_occupancy(HK.DIRTY, leaving, arriving, TODAY)

    assert result.status is OccupancyStatus.DEPARTING_TODAY
    assert result.stay == leaving
    assert result.nights_remaining == 0
    assert result.has_arrival_today


def test_out_of_order() -> None:
    result = derive_occupancy(HK.OUT_OF_ORDER, None, None, TODAY)

    assert result.status is OccupancyStatus.OUT_OF_ORDER
    assert result.stay is None
    assert not result.has_arrival_today


def test_out_of_order_room_with_arrival_flags_the_arrival() -> None:
    result = derive_occupancy(HK.OUT_OF_ORDER, None, stay(0, 2), TODAY)

    assert result.status is OccupancyStatus.OUT_OF_ORDER
    assert result.stay is None
    assert result.has_arrival_today


def test_guest_in_house_wins_over_out_of_order() -> None:
    guest = stay(-1, 2)

    result = derive_occupancy(HK.OUT_OF_ORDER, guest, None, TODAY)

    assert result.status is OccupancyStatus.OCCUPIED
    assert result.stay == guest
