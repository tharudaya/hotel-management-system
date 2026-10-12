"""Invites, account activation, password resets and forgot-password."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditEvent, AuditLog, TokenPurpose, User, UserToken
from app.services.account_tokens import FORGOT_MAX_PER_EMAIL, FORGOT_RESPONSE
from tests.conftest import MakeUser, RecordingEmailSender, Roles

USERS = "/api/admin/users"
GOOD_PASSWORD = "Tr1cky-Ferret-Lamp"  # noqa: S105
FORGOT_BODY = {"detail": FORGOT_RESPONSE}


# --- helpers ---------------------------------------------------------------------------


async def as_admin(client: AsyncClient, make_user: MakeUser) -> User:
    admin = await make_user("admin", role=Roles.ADMIN)
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-password"}
    )
    assert response.status_code == 200
    return admin


async def invited(make_user: MakeUser, db: AsyncSession, *, email: str | None = None) -> User:
    user = await make_user("newbie", role=Roles.RECEPTIONIST)
    user.full_name = "New Bie"
    user.password_hash = None
    user.email = email
    await db.flush()
    return user


def token_from(link: str) -> str:
    return link.rsplit("/", 1)[1]


def token_in_email(body: str) -> str:
    match = re.search(r"/(?:activate|reset-password)/([A-Za-z0-9_-]+)", body)
    assert match, body
    return match.group(1)


async def invite_link(client: AsyncClient, user: User) -> str:
    response = await client.post(f"{USERS}/{user.id}/invite")
    assert response.status_code == 200, response.text
    link = response.json()["link"]
    assert isinstance(link, str)
    return link


async def tokens(db: AsyncSession, user: User) -> list[UserToken]:
    query = select(UserToken).where(UserToken.user_id == user.id).order_by(UserToken.id)
    return list((await db.scalars(query.execution_options(populate_existing=True))).all())


# --- issuing invites -------------------------------------------------------------------


async def test_invite_without_email_returns_the_link_once(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, emails: RecordingEmailSender
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)

    response = await client.post(f"{USERS}/{user.id}/invite")

    assert response.status_code == 200
    data = response.json()
    assert (data["delivery"], data["sent_to"]) == ("link", None)
    assert data["link"].startswith("http://localhost:5173/activate/")
    expires = datetime.fromisoformat(data["expires_at"])
    assert timedelta(hours=71, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=72)
    assert emails.sent == []
    [row] = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "user.invite_sent"))
    ).all()
    assert row.after is not None and row.after["channel"] == "link"


async def test_invite_with_email_sends_it_and_hides_the_link(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, emails: RecordingEmailSender
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session, email="newbie@hotel.example")

    response = await client.post(f"{USERS}/{user.id}/invite")

    data = response.json()
    assert (data["delivery"], data["sent_to"], data["link"]) == (
        "email",
        "n***@hotel.example",
        None,
    )
    [message] = emails.sent
    assert message.to == "newbie@hotel.example"
    assert "newbie" in message.body and "72 hours" in message.body
    assert (
        await client.get(f"/api/auth/activate/{token_in_email(message.body)}")
    ).status_code == 200


async def test_failed_email_falls_back_to_the_link(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, emails: RecordingEmailSender
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session, email="newbie@hotel.example")
    emails.fail = True

    data = (await client.post(f"{USERS}/{user.id}/invite")).json()

    assert data["delivery"] == "link"
    assert data["link"].startswith("http://localhost:5173/activate/")


async def test_resending_an_invite_invalidates_the_older_link(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    first = token_from(await invite_link(client, user))

    second = token_from(await invite_link(client, user))

    old = await client.get(f"/api/auth/activate/{first}")
    assert old.status_code == 410
    assert old.json()["code"] == "token_expired"
    assert (await client.get(f"/api/auth/activate/{second}")).status_code == 200


@pytest.mark.parametrize(
    ("state", "endpoint", "code"),
    [
        ("active", "invite", "already_active"),
        ("invited", "reset-password", "not_activated"),
        ("deactivated", "invite", "invalid_transition"),
        ("expired", "reset-password", "invalid_transition"),
    ],
)
async def test_links_only_for_the_right_kind_of_user(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    state: str,
    endpoint: str,
    code: str,
) -> None:
    await as_admin(client, make_user)
    user = await make_user(
        "target",
        deactivated=state == "deactivated",
        expires_at=datetime.now(UTC) - timedelta(days=1) if state == "expired" else None,
    )
    if state == "invited":
        user.password_hash = None
        await db_session.flush()

    response = await client.post(f"{USERS}/{user.id}/{endpoint}")

    assert response.status_code == 409
    assert response.json()["code"] == code


async def test_cannot_issue_links_for_a_more_privileged_user(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("gm", role=Roles.GENERAL_MANAGER)
    await client.post("/api/auth/login", json={"username": "gm", "password": "gm-password"})
    boss = await make_user("boss", role=Roles.ADMIN)

    response = await client.post(f"{USERS}/{boss.id}/reset-password")

    assert response.status_code == 403


# --- activation --------------------------------------------------------------------------


async def test_activation_link_reveals_only_username_and_name(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, emails: RecordingEmailSender
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session, email="newbie@hotel.example")
    await client.post(f"{USERS}/{user.id}/invite")
    token = token_in_email(emails.sent[-1].body)

    response = await client.get(f"/api/auth/activate/{token}")

    assert response.status_code == 200
    assert response.json() == {"username": "newbie", "full_name": "New Bie"}


async def test_activate_sets_the_password_without_logging_in(
    client: AsyncClient,
    second_client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))

    response = await second_client.post(
        f"/api/auth/activate/{token}", json={"password": GOOD_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json() == {"detail": "Your account is ready. You can now sign in."}
    assert "set-cookie" not in response.headers  # not logged in
    assert (await second_client.get("/api/auth/me")).status_code == 401
    login = await second_client.post(
        "/api/auth/login", json={"username": "newbie", "password": GOOD_PASSWORD}
    )
    assert login.status_code == 200
    detail = (await client.get(f"{USERS}/{user.id}")).json()
    assert detail["status"] == "active"
    [row] = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "user.activated"))
    ).all()
    assert (row.actor_user_id, row.before, row.after) == (
        user.id,
        {"status": "invited"},
        {"status": "active", "sessions_revoked": 0},
    )


async def test_token_cannot_be_reused(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))
    assert (
        await client.post(f"/api/auth/activate/{token}", json={"password": GOOD_PASSWORD})
    ).status_code == 200

    again = await client.post(
        f"/api/auth/activate/{token}", json={"password": "Another-Fine-Pass9"}
    )
    check = await client.get(f"/api/auth/activate/{token}")

    assert again.status_code == check.status_code == 410
    assert again.json() == {"detail": "This link has already been used.", "code": "token_used"}


async def test_expired_token(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))
    await db_session.execute(
        update(UserToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    get = await client.get(f"/api/auth/activate/{token}")
    post = await client.post(f"/api/auth/activate/{token}", json={"password": GOOD_PASSWORD})

    assert get.status_code == post.status_code == 410
    assert post.json()["code"] == "token_expired"
    await db_session.refresh(user)
    assert user.password_hash is None


@pytest.mark.parametrize(
    "token",
    ["x" * 43, "A" * 20],  # well-formed but unknown
)
async def test_wrong_token(client: AsyncClient, token: str) -> None:
    for response in (
        await client.get(f"/api/auth/activate/{token}"),
        await client.post(f"/api/auth/activate/{token}", json={"password": GOOD_PASSWORD}),
        await client.get(f"/api/auth/reset-password/{token}"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "This link is not valid.", "code": "token_invalid"}


@pytest.mark.parametrize("token", ["short", "has spaces in it......", "x" * 129, "bad%2Fslash" * 3])
async def test_malformed_token(client: AsyncClient, token: str) -> None:
    assert (await client.get(f"/api/auth/activate/{token}")).status_code in (404, 422)


async def test_invite_token_does_not_work_as_a_reset_token(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))

    assert (await client.get(f"/api/auth/reset-password/{token}")).status_code == 404


async def test_link_dies_when_the_user_is_deactivated(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))
    await client.post(f"{USERS}/{user.id}/deactivate")

    response = await client.post(f"/api/auth/activate/{token}", json={"password": GOOD_PASSWORD})

    assert response.status_code == 404


# --- password rules ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short-pass1", "password_too_short"),
        ("password2024!", "password_too_common"),
        ("123qwerty123456", "password_too_common"),
        ("aaaaaaaaaaaaaa", "password_too_common"),
        ("my-newbie-account-pw", "password_contains_username"),
    ],
)
async def test_password_rules(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, password: str, code: str
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))

    response = await client.post(f"/api/auth/activate/{token}", json={"password": password})

    assert response.status_code == 422
    assert response.json()["code"] == code
    # The link still works for another attempt.
    assert (await client.get(f"/api/auth/activate/{token}")).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [{}, {"password": ""}, {"password": "x" * 1025}, {"password": "Ok-password-123", "extra": 1}],
)
async def test_set_password_validation(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, payload: dict[str, object]
) -> None:
    await as_admin(client, make_user)
    user = await invited(make_user, db_session)
    token = token_from(await invite_link(client, user))

    assert (await client.post(f"/api/auth/activate/{token}", json=payload)).status_code == 422


# --- password reset ----------------------------------------------------------------------------


async def test_admin_reset_then_user_sets_new_password_and_old_sessions_end(
    client: AsyncClient,
    second_client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
) -> None:
    await as_admin(client, make_user)
    user = await make_user("alice")
    await second_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password"}
    )
    response = await client.post(f"{USERS}/{user.id}/reset-password")
    data = response.json()
    expires = datetime.fromisoformat(data["expires_at"])
    assert timedelta(minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=1)
    token = token_from(data["link"])
    assert data["link"].startswith("http://localhost:5173/reset-password/")
    assert (await client.get(f"/api/auth/reset-password/{token}")).json() == {
        "username": "alice",
        "full_name": "Alice",
    }

    reset = await client.post(f"/api/auth/reset-password/{token}", json={"password": GOOD_PASSWORD})

    assert reset.status_code == 200
    assert (await second_client.get("/api/auth/me")).status_code == 401  # logged out
    old = await second_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password"}
    )
    new = await second_client.post(
        "/api/auth/login", json={"username": "alice", "password": GOOD_PASSWORD}
    )
    assert (old.status_code, new.status_code) == (401, 200)


# --- forgot password ------------------------------------------------------------------------------


async def test_forgot_password_sends_a_reset_link(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, emails: RecordingEmailSender
) -> None:
    user = await make_user("alice")
    user.email = "alice@hotel.example"
    await db_session.flush()

    response = await client.post(
        "/api/auth/forgot-password", json={"email": " Alice@Hotel.Example "}
    )

    assert (response.status_code, response.json()) == (202, FORGOT_BODY)
    [message] = emails.sent
    assert message.to == "alice@hotel.example" and "1 hour" in message.body
    token = token_in_email(message.body)
    assert (await client.get(f"/api/auth/reset-password/{token}")).status_code == 200
    [token_row] = await tokens(db_session, user)
    assert token_row.created_by is None


@pytest.mark.parametrize("case", ["unknown", "invited", "deactivated", "expired"])
async def test_forgot_password_answers_the_same_and_sends_nothing(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    emails: RecordingEmailSender,
    case: str,
) -> None:
    if case != "unknown":
        user = await make_user(
            "alice",
            deactivated=case == "deactivated",
            expires_at=datetime.now(UTC) - timedelta(days=1) if case == "expired" else None,
        )
        user.email = "alice@hotel.example"
        if case == "invited":
            user.password_hash = None
        await db_session.flush()

    response = await client.post("/api/auth/forgot-password", json={"email": "alice@hotel.example"})

    assert (response.status_code, response.json()) == (202, FORGOT_BODY)
    assert emails.sent == []
    event = await db_session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    assert event is not None and event.details is not None
    assert event.details["outcome"] == ("unknown_email" if case == "unknown" else "not_eligible")


async def test_forgot_password_is_throttled_silently(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, emails: RecordingEmailSender
) -> None:
    user = await make_user("alice")
    user.email = "alice@hotel.example"
    await db_session.flush()

    responses = [
        await client.post("/api/auth/forgot-password", json={"email": "alice@hotel.example"})
        for _ in range(FORGOT_MAX_PER_EMAIL + 2)
    ]

    assert {(r.status_code, r.json()["detail"]) for r in responses} == {(202, FORGOT_RESPONSE)}
    assert len(emails.sent) == FORGOT_MAX_PER_EMAIL


@pytest.mark.parametrize("payload", [{}, {"email": "not-an-email"}, {"email": "a@b.c", "x": 1}])
async def test_forgot_password_validation(client: AsyncClient, payload: dict[str, object]) -> None:
    assert (await client.post("/api/auth/forgot-password", json=payload)).status_code == 422


# --- the plain token is never stored ---------------------------------------------------


async def test_plain_tokens_never_reach_the_database(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    emails: RecordingEmailSender,
) -> None:
    await as_admin(client, make_user)
    newbie = await invited(make_user, db_session)
    alice = await make_user("alice")
    alice.email = "alice@hotel.example"
    await db_session.flush()
    invite = token_from(await invite_link(client, newbie))
    await client.post(f"{USERS}/{alice.id}/reset-password")
    reset = token_in_email(emails.sent[-1].body)
    await client.post("/api/auth/forgot-password", json={"email": "alice@hotel.example"})
    forgot = token_in_email(emails.sent[-1].body)
    await client.post(f"/api/auth/activate/{invite}", json={"password": GOOD_PASSWORD})

    # Every text column of every table, as Postgres sees it.
    tables = (
        await db_session.scalars(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
    ).all()
    dump: list[str] = []
    for table in tables:
        query = text(f'SELECT t::text FROM "{table}" t')  # noqa: S608 (catalog names)
        rows = await db_session.execute(query)
        dump.extend(row[0] for row in rows)
    everything = "\n".join(dump)

    for token in (invite, reset, forgot):
        assert token not in everything
    assert len(await tokens(db_session, newbie)) == 1  # stored as hashes only
    assert all(len(t.token_hash) == 64 for t in await tokens(db_session, newbie))
    assert TokenPurpose.INVITE.value in everything  # sanity: the dump did read user_tokens
    assert AuditAction.USER_ACTIVATED.value in everything
