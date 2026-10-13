"""Creating the first admin from the command line."""

import asyncio
import io
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.permissions import ALL_PERMISSION_KEYS
from app.models import AuditLog, User
from app.scripts import create_admin
from app.services.bootstrap import create_first_admin
from app.services.errors import DomainError, InvalidInputError
from app.services.permissions import get_default_property_id, get_effective_permissions
from tests.conftest import MakeUser, Roles

PASSWORD = "Sturdy-Lantern-Cove"


async def test_creates_an_active_admin_on_a_fresh_install(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await create_first_admin(
        db_session, username=" Root ", full_name="Ada Admin", email=None, password=PASSWORD
    )

    assert user.username == "root"
    permissions = await get_effective_permissions(
        db_session, user.id, await get_default_property_id(db_session)
    )
    assert permissions == ALL_PERMISSION_KEYS
    login = await client.post("/api/auth/login", json={"username": "root", "password": PASSWORD})
    assert login.status_code == 200
    [row] = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "user.created"))
    ).all()
    assert row.actor_user_id is None
    assert row.after is not None and row.after["created_by"] == "create_admin command"
    assert PASSWORD not in str(row.after)


async def test_refused_when_an_active_admin_exists(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    await make_user("boss", role=Roles.ADMIN)

    with pytest.raises(DomainError) as exc:
        await create_first_admin(
            db_session, username="root", full_name="R", email=None, password=PASSWORD
        )
    assert exc.value.code == "admin_exists"


@pytest.mark.parametrize("state", ["deactivated", "expired", "invited"])
async def test_allowed_to_recover_when_no_admin_can_sign_in(
    db_session: AsyncSession, make_user: MakeUser, state: str
) -> None:
    boss = await make_user(
        "boss",
        role=Roles.ADMIN,
        deactivated=state == "deactivated",
        expires_at=datetime.now(UTC) - timedelta(days=1) if state == "expired" else None,
    )
    if state == "invited":
        boss.password_hash = None
        await db_session.flush()

    user = await create_first_admin(
        db_session, username="root", full_name="R", email=None, password=PASSWORD
    )

    assert user.id != boss.id


@pytest.mark.parametrize(
    ("username", "password", "error"),
    [
        ("root", "short", InvalidInputError),
        ("root", "password2024!", InvalidInputError),
        ("root", "my-root-account-pw", InvalidInputError),
    ],
)
async def test_password_rules_apply(
    db_session: AsyncSession, username: str, password: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await create_first_admin(
            db_session, username=username, full_name="R", email=None, password=password
        )
    assert await db_session.scalar(select(User).where(User.username == username)) is None


async def test_taken_username(db_session: AsyncSession, make_user: MakeUser) -> None:
    await make_user("root", role=None)

    with pytest.raises(DomainError) as exc:
        await create_first_admin(
            db_session, username="ROOT", full_name="R", email=None, password=PASSWORD
        )
    assert exc.value.code == "username_taken"


async def test_command_line_with_password_on_stdin(
    test_settings: Settings,
    live_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(create_admin, "get_settings", lambda: test_settings)
    argv = ["--username", "root", "--full-name", "Ada Admin", "--password-stdin"]

    async def run_cli() -> int:
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}\n"))
        # main() starts its own event loop: run it on a worker thread.
        return await asyncio.to_thread(create_admin.main, argv)

    assert await run_cli() == 0
    assert "Created admin 'root'" in capsys.readouterr().out
    async with live_db() as db:
        assert await db.scalar(select(User.username).where(User.username == "root")) == "root"

    # A second run is refused: an active admin now exists.
    assert await run_cli() == 1
    assert "An active admin already exists (root)" in capsys.readouterr().err
