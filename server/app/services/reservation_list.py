"""The reservation list: search, filters, today's front desk views, sorting, pages.

Guest names are shown (they are needed to find a booking); contact details never are.
The views use the same definitions as the dashboard summary (services/dashboard):
arriving today = confirmed and covering tonight (late arrivals included) or checked in
today; departing today = in house and due out today or earlier, or come and gone today;
in house = checked in.
"""

from datetime import date

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Guest, Reservation, ReservationStatus, Room, RoomType
from app.schemas.reservations import ReservationListItem, ReservationListQuery, ReservationPage

SORT_COLUMNS: dict[str, ColumnElement[object]] = {
    "check_in_date": Reservation.check_in_date,  # type: ignore[dict-item]
    "check_out_date": Reservation.check_out_date,  # type: ignore[dict-item]
    "guest": func.lower(Guest.last_name + " " + Guest.first_name),
    "confirmation_code": Reservation.confirmation_code,  # type: ignore[dict-item]
    "created_at": Reservation.created_at,  # type: ignore[dict-item]
}


def _view(view: str, today: date) -> ColumnElement[bool]:
    status = Reservation.status
    confirmed = status == ReservationStatus.CONFIRMED
    in_house = status == ReservationStatus.CHECKED_IN
    checked_out = status == ReservationStatus.CHECKED_OUT
    if view == "arriving_today":
        return or_(
            and_(confirmed, Reservation.check_in_date <= today, Reservation.check_out_date > today),
            and_(in_house, Reservation.check_in_date == today),
        )
    if view == "departing_today":
        return or_(
            and_(in_house, Reservation.check_out_date <= today),
            and_(
                checked_out,
                or_(Reservation.check_out_date == today, Reservation.check_in_date == today),
            ),
        )
    return in_house


def _dates(query: ReservationListQuery) -> list[ColumnElement[bool]]:
    start, end = query.date_from, query.date_to
    if query.dates == "arriving":
        column = Reservation.check_in_date
    elif query.dates == "departing":
        column = Reservation.check_out_date
    else:
        # At least one night in [start, end]: check-in on or before end, check-out after start.
        criteria: list[ColumnElement[bool]] = []
        if end is not None:
            criteria.append(Reservation.check_in_date <= end)
        if start is not None:
            criteria.append(Reservation.check_out_date > start)
        return criteria
    return [
        *([column >= start] if start is not None else []),
        *([column <= end] if end is not None else []),
    ]


def _search(q: str) -> list[ColumnElement[bool]]:
    """A confirmation code (or its start) or every word in the guest's name."""
    words = q.lower().split()
    if not words:
        return []
    name = func.lower(Guest.first_name + " " + Guest.last_name)
    by_name = and_(*(name.contains(word, autoescape=True) for word in words))
    compact = q.strip().upper().replace(" ", "")
    return [or_(Reservation.confirmation_code.startswith(compact, autoescape=True), by_name)]


async def list_reservations(
    db: AsyncSession, query: ReservationListQuery, *, property_id: int, today: date
) -> ReservationPage:
    criteria: list[ColumnElement[bool]] = [Reservation.property_id == property_id]
    if query.status:
        criteria.append(Reservation.status.in_(query.status))
    if query.view is not None:
        criteria.append(_view(query.view, today))
    criteria += _dates(query)
    criteria += _search(query.q or "")

    base = (
        select(Reservation, Guest, RoomType, Room)
        .join(Reservation.guest)
        .join(Reservation.room_type)
        .outerjoin(Reservation.room)
        .where(*criteria)
    )
    total = await db.scalar(
        select(func.count()).select_from(Reservation).join(Reservation.guest).where(*criteria)
    )
    column = SORT_COLUMNS[query.sort.lstrip("-")]
    order = column.desc() if query.sort.startswith("-") else column.asc()
    rows = (
        await db.execute(
            base.order_by(order, Reservation.id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    return ReservationPage(
        items=[
            ReservationListItem(
                id=reservation.id,
                confirmation_code=reservation.confirmation_code,
                guest_name=f"{guest.first_name} {guest.last_name}",
                check_in_date=reservation.check_in_date,
                check_out_date=reservation.check_out_date,
                nights=(reservation.check_out_date - reservation.check_in_date).days,
                adults=reservation.adults,
                children=reservation.children,
                room_type=room_type.code,
                room_type_name=room_type.name,
                room_number=room.number if room else None,
                status=reservation.status,
                source=reservation.source,
                is_vip=reservation.is_vip,
                total_minor=reservation.total_minor,
                currency=reservation.currency,
            )
            for reservation, guest, room_type, room in rows
        ],
        total=total or 0,
        page=query.page,
        page_size=query.page_size,
    )
