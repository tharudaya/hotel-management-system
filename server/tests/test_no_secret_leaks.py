"""No password hashes, session tokens or link tokens in any admin response or log line."""

import logging
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserSession, UserToken
from tests.conftest import LoginAs, MakeUser, RecordingEmailSender, Roles

GOOD_PASSWORD = "Tr1cky-Ferret-Lamp"


async def test_admin_responses_and_logs_hold_no_secrets(
    client: AsyncClient,
    second_client: AsyncClient,
    login_as: LoginAs,
    make_user: MakeUser,
    db_session: AsyncSession,
    emails: RecordingEmailSender,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    await login_as(Roles.ADMIN)
    alice = await make_user("alice")
    alice.email = "alice@hotel.example"
    newbie = await make_user("newbie", role=Roles.RECEPTIONIST)
    newbie.password_hash = None
    await db_session.flush()
    await second_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password"}
    )
    # Issue links both ways and use one.
    link = (await client.post(f"/api/admin/users/{newbie.id}/invite")).json()["link"]
    token = link.rsplit("#", 1)[1]
    await client.post("/api/auth/activate", json={"token": token, "password": GOOD_PASSWORD})
    await client.post(f"/api/admin/users/{alice.id}/reset-password")
    emailed = re.search(r"#([A-Za-z0-9_-]+)", emails.sent[-1].body)
    assert emailed is not None

    bodies = [
        (await client.get(path)).text
        for path in (
            "/api/auth/me",
            "/api/admin/users",
            f"/api/admin/users/{alice.id}",
            f"/api/admin/users/{newbie.id}",
            "/api/admin/users/roles",
            "/api/admin/roles",
            "/api/admin/audit?limit=100",
        )
    ]
    everything = "\n".join(bodies)
    logs = caplog.text

    hashes = [h for h in (await db_session.scalars(select(User.password_hash))).all() if h]
    secrets = [
        *hashes,
        *(await db_session.scalars(select(UserSession.token_hash))).all(),
        *(await db_session.scalars(select(UserToken.token_hash))).all(),
        token,
        emailed.group(1),
    ]
    assert secrets  # sanity: there is something to look for
    for secret in secrets:
        assert secret not in everything
        assert secret not in logs
    assert "$argon2" not in everything and "$argon2" not in logs
    for key in ('"password_hash"', '"token_hash"', '"session_token"'):
        assert key not in everything
