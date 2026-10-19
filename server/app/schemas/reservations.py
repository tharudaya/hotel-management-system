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


NOT_NULL_UPDATE_FIELDS: tuple[str, ...] = (
    "check_in_date",
    "check_out_date",
    "room_type_id",
    "adults",
    "children",
    "is_vip",
)


class ReservationUpdate(BaseModel):
    """Only the fields sent change. `version` is the one the client last saw.

    A checked-in reservation accepts only check_out_date and notes. nightly_rate_minor
    sets the price of every night of the (new) stay; without it, nights that remain
    keep their price and new nights get the room type's current base rate.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    check_in_date: date | None = None
    check_out_date: date | None = None
    room_type_id: Annotated[int | None, Field(gt=0)] = None
    adults: Annotated[int | None, Field(ge=1, le=12)] = None
    children: Annotated[int | None, Field(ge=0, le=12)] = None
    # null clears the notes
    notes: ReservationNotes | None = None
    is_vip: bool | None = None
    nightly_rate_minor: int | None = Field(default=None, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def _required_fields_not_null(self) -> Self:
        cleared = [
            field
            for field in NOT_NULL_UPDATE_FIELDS
            if field in self.model_fields_set and getattr(self, field) is None
        ]
        if cleared:
            raise ValueError(f"These fields cannot be null: {', '.join(cleared)}")
        return self


class ReservationCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ReservationNoShow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class HistoryActor(BaseModel):
    id: int
    full_name: str


class ReservationHistoryEntry(BaseModel):
    """One audit log entry about the reservation, oldest first."""

    action: str
    occurred_at: datetime
    # Null for changes made by the system.
    actor: HistoryActor | None
    status_before: ReservationStatus | None
    status_after: ReservationStatus | None
    # Fields the entry changed (as recorded in the audit log).
    changed: list[str]
    reason: str | None


class ReservationView(ReservationDetail):
    history: list[ReservationHistoryEntry]
