"""Guests API: search, read, create, update, privacy of contact details."""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import DropQueryStrings
from app.core.permissions import P
from app.models import AuditLog, Guest, Role, RolePermission, UserPropertyRole
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, MakeUser, Roles
from tests.factories import new_guest


@pytest.fixture
async def guests(db_session: AsyncSession) -> dict[str, Guest]:
    pid = await get_default_property_id(db_session)
    found = {
        "ada": new_guest(pid, "Ada", "Lovelace", email="ada@example.com", phone="+46701234567"),
        "alan": new_guest(pid, "Alan", "Turing", email="alan@example.org"),
        "grace": new_guest(pid, "Grace", "Hopper", phone="+15550100"),
    }
    db_session.add_all(found.values())
    await db_session.flush()
    return found


async def login_without_contact_access(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    """A custom role with guest.view and reservation.create but not guest.view_contact."""
    pid = await get_default_property_id(db_session)
    role = Role(property_id=pid, name="Booking agent")
    db_session.add(role)
    await db_session.flush()
    db_session.add_all(
        RolePermission(role_id=role.id, permission_key=key)
        for key in (P.GUEST_VIEW, P.RESERVATION_CREATE)
    )
    user = await make_user("agent", role=None)
    db_session.add(UserPropertyRole(user_id=user.id, property_id=pid, role_id=role.id))
    await db_session.flush()
    response = await client.post(
        "/api/auth/login", json={"username": "agent", "password": "agent-password"}
    )
    assert response.status_code == 200


def names(body: dict[str, object]) -> list[str]:
    items = body["items"]
    assert isinstance(items, list)
    return [item["first_name"] for item in items]


# --- search ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("", ["Grace", "Ada", "Alan"]),  # by last name
        ("ada", ["Ada"]),
        ("LOVE", ["Ada"]),
        ("ada love", ["Ada"]),
        ("a t", ["Alan"]),
        ("example.org", ["Alan"]),
        ("70-123 45", ["Ada"]),  # phone digits, any formatting
        ("+46 70-123 45 67", ["Ada"]),
        ("5550100", ["Grace"]),
        ("nobody", []),
        ("%", []),  # LIKE wildcards are literal
    ],
)
async def test_search_with_contact_access(
    client: AsyncClient, login_as: LoginAs, guests: dict[str, Guest], q: str, found: list[str]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get("/api/guests", params={"q": q})

    assert response.status_code == 200, response.text
    assert names(response.json()) == found


async def test_search_results_include_contact_details(
    client: AsyncClient, login_as: LoginAs, guests: dict[str, Guest]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    body = (await client.get("/api/guests", params={"q": "ada"})).json()

    [ada] = body["items"]
    assert ada["email"] == "ada@example.com"
    assert ada["phone"] == "+46701234567"
    assert ada["contact_hidden"] is False
    assert (body["total"], body["page"], body["page_size"]) == (1, 1, 25)


async def test_search_without_contact_access_hides_and_ignores_contact_details(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, guests: dict[str, Guest]
) -> None:
    await login_without_contact_access(client, make_user, db_session)

    by_name = (await client.get("/api/guests", params={"q": "ada"})).json()
    [ada] = by_name["items"]
    assert (ada["email"], ada["phone"], ada["contact_hidden"]) == (None, None, True)
    # Email and phone cannot be used to find (or confirm) someone.
    for q in ("ada@example.com", "example.org", "0701234567"):
        assert (await client.get("/api/guests", params={"q": q})).json()["items"] == [], q


async def test_search_is_paginated(
    client: AsyncClient, login_as: LoginAs, guests: dict[str, Guest]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    first = (await client.get("/api/guests", params={"page_size": 2})).json()
    second = (await client.get("/api/guests", params={"page_size": 2, "page": 2})).json()

    assert (names(first), first["total"]) == (["Grace", "Ada"], 3)
    assert names(second) == ["Alan"]


async def test_search_only_finds_guests_of_this_property(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, guests: dict[str, Guest]
) -> None:
    from app.models import Property

    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    db_session.add(new_guest(other.id, "Ada", "Elsewhere"))
    await db_session.flush()
    await login_as(Roles.RECEPTIONIST)

    assert names((await client.get("/api/guests", params={"q": "ada"})).json()) == ["Ada"]


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}, {"q": "x" * 101}])
async def test_search_validates_parameters(
    client: AsyncClient, login_as: LoginAs, params: dict[str, str | int]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert (await client.get("/api/guests", params=params)).status_code == 422


# --- read --------------------------------------------------------------------------------


async def test_get_guest(client: AsyncClient, login_as: LoginAs, guests: dict[str, Guest]) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(f"/api/guests/{guests['ada'].id}")

    assert response.status_code == 200
    body = response.json()
    assert (body["first_name"], body["email"], body["contact_hidden"]) == (
        "Ada",
        "ada@example.com",
        False,
    )


async def test_get_guest_hides_contact_without_permission(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, guests: dict[str, Guest]
) -> None:
    await login_without_contact_access(client, make_user, db_session)

    body = (await client.get(f"/api/guests/{guests['ada'].id}")).json()

    assert (body["email"], body["phone"], body["contact_hidden"]) == (None, None, True)


async def test_get_unknown_guest(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get("/api/guests/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "guest_not_found"


# --- create and update -------------------------------------------------------------------


async def test_create_guest_normalises_input(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    user = await login_as(Roles.RECEPTIONIST)

    response = await client.post(
        "/api/guests",
        json={
            "first_name": "  Åsa ",
            "last_name": " Lindqvist",
            "email": " Asa.Lindqvist@Example.COM ",
            "phone": "+46 (0)70-123 45 67",
            "notes": "",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert {k: body[k] for k in ("first_name", "last_name", "email", "phone", "notes")} == {
        "first_name": "Åsa",
        "last_name": "Lindqvist",
        "email": "asa.lindqvist@example.com",
        "phone": "+460701234567",
        "notes": None,
    }
    [audit] = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "guest.created"))
    ).all()
    assert (audit.actor_user_id, audit.target_type, audit.target_id) == (
        user.id,
        "guest",
        str(body["id"]),
    )
    # Contact details are recorded as present, never by value.
    assert audit.after == {
        "first_name": "Åsa",
        "last_name": "Lindqvist",
        "notes": None,
        "contact": {"email": "set", "phone": "set"},
    }


@pytest.mark.parametrize(
    "body",
    [
        {"first_name": "", "last_name": "X"},
        {"first_name": "   ", "last_name": "X"},
        {"first_name": "A" * 101, "last_name": "X"},
        {"last_name": "X"},
        {"first_name": "A", "last_name": "X", "email": "not-an-email"},
        {"first_name": "A", "last_name": "X", "email": "a@b"},
        {"first_name": "A", "last_name": "X", "phone": "call me"},
        {"first_name": "A", "last_name": "X", "phone": "123"},
        {"first_name": "A", "last_name": "X", "vip": True},
    ],
)
async def test_create_guest_validation(
    client: AsyncClient, login_as: LoginAs, body: dict[str, object]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert (await client.post("/api/guests", json=body)).status_code == 422


async def test_update_guest(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, guests: dict[str, Guest]
) -> None:
    await login_as(Roles.RECEPTIONIST)
    ada = guests["ada"]

    response = await client.patch(
        f"/api/guests/{ada.id}",
        json={"last_name": "King", "email": None, "phone": "+46 70 999 99 99"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["first_name"], body["last_name"], body["email"], body["phone"]) == (
        "Ada",
        "King",
        None,
        "+46709999999",
    )
    [audit] = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "guest.updated"))
    ).all()
    assert audit.before == {
        "last_name": "Lovelace",
        "contact": {"email": "set", "phone": "set"},
    }
    assert audit.after == {"last_name": "King", "contact": {"email": "empty", "phone": "set"}}


async def test_update_without_changes_writes_no_audit(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, guests: dict[str, Guest]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.patch(f"/api/guests/{guests['ada'].id}", json={"first_name": "Ada"})

    assert response.status_code == 200
    assert not (await db_session.scalars(select(AuditLog))).all()


@pytest.mark.parametrize(
    "body", [{"first_name": None}, {"last_name": ""}, {"email": "nope"}, {"id": 5}]
)
async def test_update_guest_validation(
    client: AsyncClient, login_as: LoginAs, guests: dict[str, Guest], body: dict[str, object]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert (await client.patch(f"/api/guests/{guests['ada'].id}", json=body)).status_code == 422


async def test_update_unknown_guest(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.patch("/api/guests/999999", json={"first_name": "X"})

    assert response.status_code == 404


# --- permissions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "read", "write"),
    [
        (Roles.RECEPTIONIST, 200, 201),
        (Roles.FRONT_OFFICE_MANAGER, 200, 201),
        (Roles.HOUSEKEEPING_SUPERVISOR, 403, 403),
        (Roles.ROOM_ATTENDANT, 403, 403),
    ],
)
async def test_guest_permissions(
    client: AsyncClient, login_as: LoginAs, role: str, read: int, write: int
) -> None:
    await login_as(role)

    assert (await client.get("/api/guests")).status_code == read
    created = await client.post("/api/guests", json={"first_name": "A", "last_name": "B"})
    assert created.status_code == write


async def test_guests_require_login(client: AsyncClient) -> None:
    assert (await client.get("/api/guests")).status_code == 401
    assert (await client.post("/api/guests", json={})).status_code == 401


# --- logs --------------------------------------------------------------------------------


async def test_guest_data_never_reaches_the_logs(
    client: AsyncClient,
    login_as: LoginAs,
    guests: dict[str, Guest],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await login_as(Roles.RECEPTIONIST)
    caplog.set_level(logging.DEBUG)

    await client.get("/api/guests", params={"q": "ada@example.com"})
    await client.post(
        "/api/guests",
        json={"first_name": "Zed", "last_name": "Secret", "email": "zed@secret.example"},
    )
    await client.post("/api/guests", json={"first_name": "Bad", "email": "bad@secret.example"})

    logged = caplog.text + "".join(str(r.args) for r in caplog.records)
    for secret in ("ada@example.com", "zed@secret.example", "bad@secret.example", "Secret"):
        assert secret not in logged


def test_access_log_lines_drop_query_strings() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/guests?q=ada%40example.com", "1.1", 200),
        None,
    )

    assert DropQueryStrings().filter(record)

    assert record.getMessage() == '127.0.0.1:5000 - "GET /api/guests?[redacted] HTTP/1.1" 200'
    assert "ada" not in record.getMessage()
