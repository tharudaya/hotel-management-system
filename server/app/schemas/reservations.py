from datetime import date

from pydantic import BaseModel

from app.models import ReservationStatus


class GuestOut(BaseModel):
    first_name: str
    last_name: str
    email: str | None
    phone: str | None


class ReservationDetail(BaseModel):
    id: int
    confirmation_code: str
    status: ReservationStatus
    check_in_date: date
    check_out_date: date
    nights: int
    adults: int
    children: int
    is_vip: bool
    notes: str | None
    room_type: str
    room_type_name: str
    room_id: int | None
    room_number: str | None
    guest: GuestOut
