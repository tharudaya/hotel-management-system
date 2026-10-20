"""Request and response models for /api/admin/room-types and /api/admin/rooms."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models import HousekeepingStatus

# Stored upper-case (the service normalises), so uniqueness is case-insensitive.
RoomTypeCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$"
    ),
]
RoomTypeName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
# Sensible ceiling for one room (family suites, dormitories excluded).
MAX_OCCUPANCY_LIMIT = 12
MaxOccupancy = Annotated[int, Field(ge=1, le=MAX_OCCUPANCY_LIMIT)]
BedSetup = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
RoomNumber = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=10, pattern=r"^[A-Za-z0-9-]+$"
    ),
]
Floor = Annotated[int, Field(ge=0, le=200)]
Version = Annotated[int, Field(ge=1)]


def _not_null(value: object) -> object:
    if value is None:
        raise ValueError("cannot be cleared")
    return value


# --- room types ---------------------------------------------------------------------


class RoomTypeListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_retired: bool = False


class RoomTypeOut(BaseModel):
    id: int
    code: str
    name: str
    max_occupancy: int
    bed_setup: str | None
    description: str | None
    # Default price per night in minor units of the property currency (0 = not set yet:
    # bookings then need an explicit price).
    base_rate_minor: int
    is_active: bool
    active_room_count: int
    # Confirmed stays not yet over, plus guests in house, booked as this type.
    upcoming_reservations: int
    # Why it cannot be retired right now (UI hint; the server re-checks). None = it can.
    retire_blocked: str | None
    created_at: datetime
    updated_at: datetime


BaseRate = Annotated[int, Field(ge=0, le=100_000_000)]


class RoomTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: RoomTypeCode
    name: RoomTypeName
    max_occupancy: MaxOccupancy
    bed_setup: BedSetup | None = None
    description: LongText | None = None
    base_rate_minor: BaseRate = 0


class RoomTypeUpdate(BaseModel):
    """Only the fields sent change. bed_setup and description can be cleared with null."""

    model_config = ConfigDict(extra="forbid")

    code: RoomTypeCode | None = None
    name: RoomTypeName | None = None
    max_occupancy: MaxOccupancy | None = None
    bed_setup: BedSetup | None = None
    description: LongText | None = None
    # A new price applies to bookings made from now on; existing ones keep theirs.
    base_rate_minor: BaseRate | None = None

    _required = field_validator("code", "name", "max_occupancy", "base_rate_minor")(_not_null)


# --- rooms -------------------------------------------------------------------------


class RoomListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    floor: Floor | None = None
    room_type_id: Annotated[int | None, Field(gt=0)] = None
    status: Literal["active", "retired", "all"] = "active"


class RoomTypeRef(BaseModel):
    id: int
    code: str
    name: str
    is_active: bool


class UserRef(BaseModel):
    id: int
    username: str
    full_name: str


class RoomOut(BaseModel):
    id: int
    number: str
    floor: int
    room_type: RoomTypeRef
    notes: str | None
    housekeeping_status: HousekeepingStatus
    is_active: bool
    retired_at: datetime | None
    retired_by: UserRef | None
    guest_in_house: bool
    # Confirmed reservations assigned to this room that are not over yet.
    upcoming_reservations: int
    # Why each change is blocked right now (UI hints; the server re-checks). None = OK.
    retire_blocked: str | None
    type_change_blocked: str | None
    # Send back on edit/retire/reactivate (optimistic locking).
    version: int
    created_at: datetime
    updated_at: datetime


class RoomCreate(BaseModel):
    """New rooms start with housekeeping status inspected (Ready)."""

    model_config = ConfigDict(extra="forbid")

    number: RoomNumber
    floor: Floor
    room_type_id: Annotated[int, Field(gt=0)]
    notes: LongText | None = None


class RoomUpdate(BaseModel):
    """Only the fields sent change; notes can be cleared with null."""

    model_config = ConfigDict(extra="forbid")

    version: Version
    number: RoomNumber | None = None
    floor: Floor | None = None
    room_type_id: Annotated[int | None, Field(gt=0)] = None
    notes: LongText | None = None

    _required = field_validator("number", "floor", "room_type_id")(_not_null)


class RoomVersion(BaseModel):
    """Body of retire/reactivate: the version the client saw."""

    model_config = ConfigDict(extra="forbid")

    version: Version
