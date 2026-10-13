"""The expired-users sweep: revoke sessions, close sockets, audit once; one sweeper at
a time; runs in the API process and from the CLI."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import AuditLog, User, UserSession
from app.realtime.bus import EventBus
from app.realtime.events import DomainEvent, PermissionsChanged, SessionEnded
from app.scripts import expire_users
from app.services.expiry import SWEEP_LOCK_ID, sweep_expired_users
from tests.conftest import MakeUser, Roles, live_clients
from tests.test_realtime import connect, expect_close, live, login, receive, seeded

__all__ = ["seeded"]

# Real time: the audit mark gets the database clock, which must agree with "now".
NOW = datetime.now(UTC).replace(microsecond=0)


def recording_bus() -> tuple[EventBus, list[DomainEvent]]:
    bus = EventBus()
    events: list[DomainEvent] = []

    async def collect(event: DomainEvent) -> None:
        events.append(event)

    bus.subscribe(collect)
    return bus, events


async def open_session(db: AsyncSession, user: User, token_hash: str) -> None:
    db.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=NOW + timedelta(hours=8)))
    await db.flush()


async def expired_marks(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog).where(AuditLog.action == "user.expired").order_by(AuditLog.id)
    )
    return list(rows.all())


async def test_sweep_revokes_sessions_and_records_once(
    make_user: MakeUser, db_session: AsyncSession
) -> None:
    temp = await make_user("temp", expires_at=NOW - timedelta(minutes=5))
    await open_session(db_session, temp, "a" * 64)
    await open_session(db_session, temp, "b" * 64)
    bus, events = recording_bus()

    result = await sweep_expired_users(db_session, bus, now=NOW)

    assert (result.ran, result.expired_user_ids, result.sessions_revoked) == (True, [temp.id], 2)
    sessions = (
        await db_session.scalars(
            select(UserSession)
            .where(UserSession.user_id == temp.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert all(s.revoked_at == NOW for s in sessions)
    [mark] = await expired_marks(db_session)
    assert (mark.actor_user_id, mark.target_id) == (None, str(temp.id))
    assert mark.after == {
        "expires_at": (NOW - timedelta(minutes=5)).isoformat(),
        "sessions_revoked": 2,
    }
    ended = [e for e in events if isinstance(e, SessionEnded)]
    assert sorted(e.session_token_hash for e in ended) == ["a" * 64, "b" * 64]
    assert {e.reason for e in ended} == {"Account expired"}
    changed = [e for e in events if isinstance(e, PermissionsChanged)]
    assert [e.user_ids for e in changed] == [[temp.id]]

    # The audit entry marks the user as done: a second sweep finds nothing.
    again = await sweep_expired_users(db_session, EventBus(), now=NOW + timedelta(minutes=5))
    assert again.expired_user_ids == []
    assert len(await expired_marks(db_session)) == 1


async def test_sweep_leaves_everyone_else_alone(
    make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("permanent")
    later = await make_user("later", expires_at=NOW + timedelta(days=1))
    await make_user("gone", deactivated=True, expires_at=NOW - timedelta(days=1))
    await open_session(db_session, later, "c" * 64)

    result = await sweep_expired_users(db_session, EventBus(), now=NOW)

    assert result.expired_user_ids == []
    assert await expired_marks(db_session) == []


async def test_expired_user_without_sessions_is_still_marked(
    make_user: MakeUser, db_session: AsyncSession
) -> None:
    temp = await make_user("temp", expires_at=NOW - timedelta(days=3))

    result = await sweep_expired_users(db_session, EventBus(), now=NOW)

    assert (result.expired_user_ids, result.sessions_revoked) == ([temp.id], 0)


async def test_an_extended_then_lapsed_expiry_is_processed_again(
    make_user: MakeUser, db_session: AsyncSession
) -> None:
    temp = await make_user("temp", expires_at=NOW - timedelta(days=1))
    await sweep_expired_users(db_session, EventBus(), now=NOW)
    # An admin extends their access by a week; it then runs out again.
    await db_session.execute(
        update(User).where(User.id == temp.id).values(expires_at=NOW + timedelta(days=7))
    )

    result = await sweep_expired_users(db_session, EventBus(), now=NOW + timedelta(days=8))

    assert result.expired_user_ids == [temp.id]
    assert len(await expired_marks(db_session)) == 2


async def test_only_one_sweeper_at_a_time(
    make_user: MakeUser, db_session: AsyncSession, test_settings: Settings
) -> None:
    await make_user("temp", expires_at=NOW - timedelta(days=1))
    engine = create_async_engine(test_settings.database_url)
    try:
        async with engine.connect() as other:
            # Another instance is sweeping (holds the lock on its own connection).
            await other.execute(text(f"SELECT pg_advisory_lock({SWEEP_LOCK_ID})"))

            result = await sweep_expired_users(db_session, EventBus(), now=NOW)

            await other.execute(text(f"SELECT pg_advisory_unlock({SWEEP_LOCK_ID})"))
    finally:
        await engine.dispose()
    assert (result.ran, result.expired_user_ids) == (False, [])
    assert await expired_marks(db_session) == []


# --- running for real: sockets, the background task, the CLI ----------------------------


async def expire(live_db: async_sessionmaker[AsyncSession], username: str) -> None:
    async with live_db() as db:
        await db.execute(
            update(User)
            .where(User.username == username)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()


async def test_sweep_closes_open_sockets_at_once(
    test_settings: Settings, seeded: dict[str, int], live_db: async_sessionmaker[AsyncSession]
) -> None:
    async with live_clients(test_settings) as make:
        app, desk = await live(make)  # Receptionist "frontdesk"; heartbeat 25 s
        async with connect(desk) as ws:
            await receive(ws)
            await expire(live_db, "frontdesk")

            async with app.state.session_factory() as db:
                result = await sweep_expired_users(db, app.state.event_bus)

            assert result.sessions_revoked == 1
            await expect_close(ws, 4401, "Account expired")


async def test_the_api_process_sweeps_on_its_own(
    test_settings: Settings, seeded: dict[str, int], live_db: async_sessionmaker[AsyncSession]
) -> None:
    async with live_clients(test_settings) as make:
        app, desk = await live(make, expiry_sweep_interval_seconds=1)
        await expire(live_db, "frontdesk")

        for _ in range(40):  # up to ~4 s
            async with live_db() as db:
                marked = await db.scalar(
                    select(AuditLog.id).where(AuditLog.action == "user.expired")
                )
            if marked is not None:
                break
            await asyncio.sleep(0.1)
        assert marked is not None
        assert (await desk.get("/api/auth/me")).status_code == 401
        assert isinstance(app, FastAPI)


async def test_cli_sweeps_once(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await expire(live_db, "attendant")
    monkeypatch.setattr(expire_users, "get_settings", lambda: test_settings)

    assert await expire_users.main() == 0

    assert "Expired users processed: 1; sessions revoked: 0" in capsys.readouterr().out


async def test_expired_user_is_refused_even_before_the_sweep(
    test_settings: Settings, seeded: dict[str, int], live_db: async_sessionmaker[AsyncSession]
) -> None:
    """The sweep is housekeeping: access already ends the moment the date passes."""
    async with live_clients(test_settings) as make:
        _, desk = await live(make)
        await expire(live_db, "frontdesk")

        assert (await desk.get("/api/auth/me")).status_code == 401
        await login(desk, Roles.ADMIN)  # other users are unaffected
