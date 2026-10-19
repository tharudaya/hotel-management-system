from datetime import date, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models import ReservationSource, ReservationStatus
from app.schemas.availability import NightlyRate
from app.schemas.guests import GuestCreate


class GuestOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    # Null without guest.view_contact (then contact_hidden is true).
    email: str | None
    phone: str | None
    contact_hidden: bool


class ReservationDetail(BaseModel):
    id: int
    confirmation_code: str
    status: ReservationStatus
    source: ReservationSource
    check_in_date: date
    check_out_date: date
    nights: int
    adults: int
    children: int
    is_vip: bool
    notes: str | None
    room_type_id: int
    room_type: str
    room_type_name: str
    room_id: int | None
    room_number: str | None
    guest: GuestOut
    # Price snapshot taken at booking, in minor units of `currency`.
    nightly_rates: list[NightlyRate]
    total_minor: int
    currency: str
    # Send it back when changing the reservation (optimistic locking).
    version: int
    created_at: datetime


ReservationNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class ReservationCreate(BaseModel):
    """A new booking for an existing guest (`guest_id`) or a new one (`guest`)."""

    model_config = ConfigDict(extra="forbid")

    guest_id: Annotated[int | None, Field(gt=0)] = None
    guest: GuestCreate | None = None
    room_type_id: int = Field(gt=0)
    check_in_date: date
    check_out_date: date
    adults: int = Field(default=1, ge=1, le=12)
    children: int = Field(default=0, ge=0, le=12)
    source: ReservationSource
    notes: ReservationNotes | None = None
    is_vip: bool = False
    # Price per night instead of the room type's base rate; needs reservation.override_rate
    # unless it equals the base rate.
    nightly_rate_minor: int | None = Field(default=None, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def _one_guest(self) -> Self:
        if (self.guest_id is None) == (self.guest is None):
            raise ValueError("Give either guest_id (an existing guest) or guest (a new one)")
        return self
