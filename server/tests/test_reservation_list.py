"""GET /api/reservations: search, filters, today's views, sorting, pagination, access."""

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import Property, ReservationStatus, Room, RoomType
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles
from tests.factories import new_guest, new_reservation

TODAY: date = date(2026, 3, 10)
RS = ReservationStatus
URL = "/api/reservations"


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@pytest.fixture
async def bookings(db_session: AsyncSession, app: FastAPI) -> dict[str, int]:
    """Named bookings (by guest first name) -> id."""
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    pid = await get_default_property_id(db_session)
    std = RoomType(property_id=pid, code="STD", name="Standard", max_occupancy=2)
    rooms = {
        n: Room(property_id=pid, number=n, floor=1, room_type=std) for n in ("101", "102", "103")
    }
    db_session.add_all(rooms.values())
    plan = [
        # first name, last name, check-in, check-out, status, room, code
        ("Ada", "Lovelace", 0, 2, RS.CONFIRMED, None, "ADAX2345"),  # arriving today
        ("Alan", "Turing", -1, 1, RS.CONFIRMED, "101", "MXQT2345"),  # late arrival
        ("Grace", "Hopper", -2, 0, RS.CHECKED_IN, "102", "GRAC2345"),  # departing today
        ("Edsger", "Dijkstra", -3, 2, RS.CHECKED_IN, "103", "EDSG2345"),  # in house
        ("Barbara", "Liskov", 5, 7, RS.CONFIRMED, None, "BARB2345"),  # future
        ("Ken", "Thompson", 0, 3, RS.CANCELLED, None, "KENT2345"),
        ("Linus", "Torvalds", -6, -4, RS.CHECKED_OUT, "101", "TRVD2345"),  # past
    ]
    ids: dict[str, int] = {}
    for first, last, check_in, check_out, status, room, code in plan:
        reservation = new_reservation(
            std,
            check_in_date=day(check_in),
            check_out_date=day(check_out),
            status=status,
            room=rooms[room] if room else None,
            guest=new_guest(pid, first, last, email=f"{first.lower()}@example.com"),
            confirmation_code=code,
        )
        db_session.add(reservation)
        await db_session.flush()
        ids[first] = reservation.id
    return ids


async def names(client: AsyncClient, **params: str | int | list[str]) -> list[str]:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return [item["guest_name"].split()[0] for item in response.json()["items"]]


async def test_default_list_is_sorted_by_arrival(
    client: AsyncClient, login_as: LoginAs, bookings: dict[str, int]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 7
    assert [i["guest_name"].split()[0] for i in body["items"]] == [
        "Linus", "Edsger", "Grace", "Alan", "Ada", "Ken", "Barbara",
    ]  # fmt: skip
    alan = next(i for i in body["items"] if i["guest_name"] == "Alan Turing")
    assert alan == {
        "id": bookings["Alan"],
        "confirmation_code": "MXQT2345",
        "guest_name": "Alan Turing",
        "check_in_date": "2026-03-09",
        "check_out_date": "2026-03-11",
        "nights": 2,
        "adults": 1,
        "children": 0,
        "room_type": "STD",
        "room_type_name": "Standard",
        "room_number": "101",
        "status": "confirmed",
        "source": "phone",
        "is_vip": False,
        "total_minor": 200_000,
        "currency": "SEK",
    }
    # No contact details in the list.
    assert "example.com" not in response.text


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("adax2345", ["Ada"]),  # code, any case
        ("MXQ", ["Alan"]),  # the start of a code
        ("grace", ["Grace"]),
        ("hop gra", ["Grace"]),  # words in any order
        ("tur", ["Alan"]),
        ("example.com", []),  # emails are not searched
        ("%", []),
    ],
)
async def test_search(
    client: AsyncClient, login_as: LoginAs, bookings: dict[str, int], q: str, found: list[str]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert await names(client, q=q) == found


@pytest.mark.parametrize(
    ("view", "found"),
    [
        ("arriving_today", ["Alan", "Ada"]),
        ("departing_today", ["Grace"]),
        ("in_house", ["Edsger", "Grace"]),
    ],
)
async def test_todays_views(
    client: AsyncClient,
    login_as: LoginAs,
    bookings: dict[str, int],
    view: str,
    found: list[str],
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert await names(client, view=view) == found


async def test_status_filter_takes_several(
    client: AsyncClient, login_as: LoginAs, bookings: dict[str, int]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert await names(client, status=["cancelled", "checked_out"]) == ["Linus", "Ken"]


@pytest.mark.parametrize(
    ("dates", "date_from", "date_to", "found"),
    [
        ("arriving", day(0), day(0), ["Ada", "Ken"]),
        ("departing", day(0), day(2), ["Grace", "Alan", "Edsger", "Ada"]),
        ("staying", day(4), day(5), ["Barbara"]),  # only Barbara has a night there
        ("staying", day(1), day(1), ["Edsger", "Ada", "Ken"]),  # Alan leaves that morning
        ("arriving", day(1), None, ["Barbara"]),  # open-ended
    ],
)
async def test_date_range(
    client: AsyncClient,
    login_as: LoginAs,
    bookings: dict[str, int],
    dates: str,
    date_from: date,
    date_to: date | None,
    found: list[str],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    params: dict[str, str | int | list[str]] = {"dates": dates, "date_from": str(date_from)}
    if date_to is not None:
        params["date_to"] = str(date_to)

    assert sorted(await names(client, **params)) == sorted(found)


@pytest.mark.parametrize(
    ("sort", "first_three"),
    [
        ("-check_in_date", ["Barbara", "Ada", "Ken"]),
        ("check_out_date", ["Linus", "Grace", "Alan"]),
        ("guest", ["Edsger", "Grace", "Barbara"]),  # Dijkstra, Hopper, Liskov
        ("-guest", ["Alan", "Linus", "Ken"]),  # Turing, Torvalds, Thompson
        ("confirmation_code", ["Ada", "Barbara", "Edsger"]),
    ],
)
async def test_sorting(
    client: AsyncClient,
    login_as: LoginAs,
    bookings: dict[str, int],
    sort: str,
    first_three: list[str],
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert (await names(client, sort=sort))[:3] == first_three


async def test_pagination(client: AsyncClient, login_as: LoginAs, bookings: dict[str, int]) -> None:
    await login_as(Roles.RECEPTIONIST)

    first = (await client.get(URL, params={"page_size": 3})).json()
    third = (await client.get(URL, params={"page_size": 3, "page": 3})).json()

    assert (first["total"], len(first["items"]), first["page"]) == (7, 3, 1)
    assert [i["guest_name"] for i in third["items"]] == ["Barbara Liskov"]


async def test_other_properties_bookings_are_not_listed(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, bookings: dict[str, int]
) -> None:
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    theirs = RoomType(property_id=other.id, code="STD", name="Std", max_occupancy=2)
    db_session.add(
        new_reservation(theirs, check_in_date=day(0), check_out_date=day(1), first_name="Ada")
    )
    await db_session.flush()
    await login_as(Roles.RECEPTIONIST)

    assert (await client.get(URL)).json()["total"] == 7


@pytest.mark.parametrize(
    "params",
    [
        {"status": "lost"},
        {"view": "tomorrow"},
        {"dates": "sleeping"},
        {"date_from": "2026-03-12", "date_to": "2026-03-11"},
        {"sort": "price"},
        {"page": 0},
        {"page_size": 101},
        {"q": "x" * 101},
        {"colour": "red"},
    ],
)
async def test_validation(
    client: AsyncClient, login_as: LoginAs, params: dict[str, str | int]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert (await client.get(URL, params=params)).status_code == 422


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (Roles.RECEPTIONIST, 200),
        (Roles.FRONT_OFFICE_MANAGER, 200),
        (Roles.HOUSEKEEPING_SUPERVISOR, 403),
        (Roles.ROOM_ATTENDANT, 403),
    ],
)
async def test_list_needs_reservation_view(
    client: AsyncClient, login_as: LoginAs, role: str, status: int
) -> None:
    await login_as(role)

    assert (await client.get(URL)).status_code == status


async def test_list_requires_login(client: AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
