"""Rules every stay must follow, shared by availability, booking and modification.

A stay is the nights [check_in_date, check_out_date): plain dates in the property's
time zone. "Today" is the hotel's business date (HotelTodayDep).
"""

from datetime import date, timedelta

from app.models import Property
from app.services.errors import InvalidInputError


def stay_nights(check_in_date: date, check_out_date: date) -> list[date]:
    """The nights of the stay, in order (the check-out day is not a night)."""
    return [check_in_date + timedelta(days=i) for i in range((check_out_date - check_in_date).days)]


def validate_stay(
    prop: Property,
    today: date,
    *,
    check_in_date: date,
    check_out_date: date,
    adults: int,
    children: int,
    check_in_may_be_past: bool = False,
) -> None:
    """Raise InvalidInputError (422) for a stay the property does not take.

    `check_in_may_be_past` is for changes that keep the original check-in date (a guest
    in house, or a late arrival): then the past check-in and the horizon are not checked,
    but the stay must still end today or later."""
    if adults < 1:
        raise InvalidInputError("A stay needs at least one adult.", code="invalid_guests")
    if children < 0:
        raise InvalidInputError("Children cannot be negative.", code="invalid_guests")
    if check_out_date <= check_in_date:
        raise InvalidInputError(
            "Check-out must be at least one day after check-in.", code="invalid_dates"
        )
    if check_in_may_be_past:
        if check_out_date < today:
            raise InvalidInputError(
                f"Check-out {check_out_date.isoformat()} is in the past "
                f"(today is {today.isoformat()}).",
                code="check_out_in_past",
            )
    elif check_in_date < today:
        raise InvalidInputError(
            f"Check-in {check_in_date.isoformat()} is in the past (today is {today.isoformat()}).",
            code="check_in_in_past",
        )
    nights = (check_out_date - check_in_date).days
    if nights > prop.max_stay_nights:
        raise InvalidInputError(
            f"A stay can be at most {prop.max_stay_nights} nights ({nights} requested).",
            code="stay_too_long",
        )
    last_check_in = today + timedelta(days=prop.booking_horizon_days)
    if not check_in_may_be_past and check_in_date > last_check_in:
        raise InvalidInputError(
            f"Bookings open {prop.booking_horizon_days} days ahead: check-in can be "
            f"{last_check_in.isoformat()} at the latest.",
            code="beyond_booking_horizon",
        )
