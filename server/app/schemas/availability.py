from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class AvailabilityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_in: date
    check_out: date
    adults: int = Field(default=1, ge=1, le=12)
    children: int = Field(default=0, ge=0, le=12)
    # When changing a booking: leave it out of the counts, so it does not compete with
    # itself.
    exclude_reservation_id: int | None = Field(default=None, gt=0)


class NightlyRate(BaseModel):
    night_date: date
    # Minor units of `currency`.
    rate_minor: int


class RoomTypeAvailability(BaseModel):
    room_type_id: int
    code: str
    name: str
    max_occupancy: int
    bed_setup: str | None
    # Rooms of this type free on every night of the stay (0 = sold out).
    available: int
    nightly_rates: list[NightlyRate]
    total_minor: int
    currency: str


class Availability(BaseModel):
    check_in_date: date
    check_out_date: date
    nights: int
    adults: int
    children: int
    currency: str
    # Active room types that fit the party, sold-out ones included, by name.
    room_types: list[RoomTypeAvailability]
