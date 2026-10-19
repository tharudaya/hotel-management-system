from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from app.models import HousekeepingStatus, ReservationStatus


class OccupancyStatus(StrEnum):
    VACANT = "vacant"
    OCCUPIED = "occupied"
    ARRIVING_TODAY = "arriving_today"
    DEPARTING_TODAY = "departing_today"
    OUT_OF_ORDER = "out_of_order"


class DashboardRoom(BaseModel):
    room_id: int
    number: str
    floor: int
    room_type: str
    room_type_name: str
    housekeeping_status: HousekeepingStatus
    occupancy_status: OccupancyStatus
    # The in-house guest if there is one, otherwise today's arriving guest.
    guest_name: str | None
    reservation_id: int | None
    # In house: nights until check-out (0 when due out). Arriving: length of stay.
    nights_remaining: int | None
    is_vip: bool
    has_notes: bool
    # True when a guest is due to arrive today, including a turnover room whose
    # status shows the departing guest, or an out-of-order room with an arrival.
    has_arrival_today: bool
    # Room row version for optimistic locking on later updates.
    version: int


class DashboardRooms(BaseModel):
    business_date: date
    rooms: list[DashboardRoom]


class ArrivalEntry(BaseModel):
    """A booking arriving today (or late: due earlier, not here yet) or checked in today.
    Shown to every dashboard user: no contact details."""

    reservation_id: int
    confirmation_code: str
    guest_name: str
    status: ReservationStatus
    check_in_date: date
    check_out_date: date
    nights: int
    adults: int
    children: int
    room_type_id: int
    room_type: str
    room_type_name: str
    room_id: int | None
    room_number: str | None
    is_vip: bool
    has_notes: bool
    # The reservation version, for assigning a room.
    version: int


class DashboardSummary(BaseModel):
    business_date: date
    # Reservations due in today (confirmed, checked in, or come and gone), and how many
    # have arrived.
    arrivals_today: int
    arrivals_checked_in: int
    # Guests due out today (including overstays), and how many have left.
    departures_today: int
    departures_checked_out: int
    # Rooms with a guest in house right now, and the number of guests in them.
    in_house: int
    in_house_guests: int
    # Rooms free tonight, per room type, summed (as the availability service counts).
    available_tonight: int
    dirty: int
    out_of_order: int
    # Rooms sold tonight / sellable rooms, as a percentage (1 decimal).
    occupancy_percent: float
    # Today's arrivals, waiting for a room first (see ArrivalEntry).
    arrivals: list[ArrivalEntry]
