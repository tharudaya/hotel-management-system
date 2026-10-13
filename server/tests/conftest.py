"""Test fixtures.

Tests run against a separate database (TEST_DATABASE_URL). It is created if
missing and migrated to head once per test session.
"""

import asyncio
import functools
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import SERVER_DIR, Settings, get_settings
from app.core.email import EmailDeliveryError, EmailMessage
from app.core.security import hash_password
from app.db.session import create_engine, create_session_factory, get_session
from app.main import create_app
from app.models import Role, User, UserPropertyRole
from app.scripts.seed import DEV_USERS
from app.services.permissions import get_default_property_id


@functools.cache
def cached_password_hash(password: str) -> str:
    """Argon2 is deliberately slow; hash each test password once per run."""
    return hash_password(password)


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    settings = get_settings()
    if not settings.test_database_url:
        pytest.exit("TEST_DATABASE_URL is not set; see .env.example", returncode=1)
    if settings.test_database_url == settings.database_url:
        pytest.exit("TEST_DATABASE_URL must differ from DATABASE_URL", returncode=1)
    return settings.model_copy(
        update={
            "environment": "test",
            "database_url": settings.test_database_url,
            # Production-like values, independent of the developer's .env.
            "session_cookie_secure": True,
            "session_max_age_seconds": 8 * 60 * 60,
            "login_failure_window_seconds": 15 * 60,
            "login_max_failures_per_username": 5,
            "login_max_failures_per_ip": 20,
            # Tests run the sweep explicitly.
            "expiry_sweep_interval_seconds": 0,
        }
    )


async def _create_database_if_missing(database_url: str) -> None:
    url = make_url(database_url)
    # CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                # Identifiers cannot be bound as parameters; quote the name instead.
                quoted = conn.dialect.identifier_preparer.quote(str(url.database))
                await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(test_settings: Settings) -> None:
    # Sync fixture: Alembic's env.py calls asyncio.run(), which cannot run
    # inside pytest-asyncio's event loop.
    asyncio.run(_create_database_if_missing(test_settings.database_url))
    config = Config(toml_file=str(SERVER_DIR / "pyproject.toml"))
    config.set_main_option("sqlalchemy.url", test_settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


async def _client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport does not run lifespan events, so drive them explicitly.
    # https so the client sends back cookies marked Secure.
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client,
    ):
        yield client


@pytest.fixture
def app(test_settings: Settings, db_session: AsyncSession) -> FastAPI:
    """App whose requests all use the test's rolled-back session."""
    app = create_app(test_settings)

    async def use_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = use_test_session
    app.state.email_sender = RecordingEmailSender()
    return app


class RecordingEmailSender:
    """Keeps sent emails for assertions instead of printing them."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = False

    async def send(self, message: EmailMessage) -> None:
        if self.fail:
            raise EmailDeliveryError("test: delivery disabled")
        self.sent.append(message)


@pytest.fixture
def emails(app: FastAPI) -> RecordingEmailSender:
    sender: RecordingEmailSender = app.state.email_sender
    return sender


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async for c in _client_for(app):
        yield c


@pytest.fixture
async def second_client(app: FastAPI, client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Another browser on the same app (own cookies), e.g. the user being administered.

    Depends on `client`, whose context already runs the app's lifespan.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as other:
        yield other


@pytest.fixture
async def client_without_database(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Client whose database URL points at a port where nothing is listening."""
    url = make_url(test_settings.database_url).set(host="127.0.0.1", port=1)
    settings = test_settings.model_copy(
        update={"database_url": url.render_as_string(hide_password=False)}
    )
    async for c in _client_for(create_app(settings)):
        yield c


@pytest.fixture
async def db_session(test_settings: Settings) -> AsyncIterator[AsyncSession]:
    """Session inside a transaction that is rolled back after the test.

    Commits and IntegrityError rollbacks inside the test only affect a
    savepoint, so tests never leave data behind.
    """
    engine = create_engine(test_settings.database_url)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            session = AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


class Roles:
    """Names of the default system roles created by the migration."""

    ADMIN = "Admin"
    GENERAL_MANAGER = "General manager"
    FRONT_OFFICE_MANAGER = "Front office manager"
    RECEPTIONIST = "Receptionist"
    HOUSEKEEPING_SUPERVISOR = "Housekeeping supervisor"
    ROOM_ATTENDANT = "Room attendant"


# Same usernames as the dev seed, so tests read like the real accounts.
USERNAME_FOR_ROLE: dict[str, str] = {role: username for username, _, role in DEV_USERS}


async def role_id(db: AsyncSession, role_name: str) -> int:
    """Id of a role at the default property."""
    property_id = await get_default_property_id(db)
    found = await db.scalar(
        select(Role.id).where(Role.property_id == property_id, Role.name == role_name)
    )
    if found is None:
        raise LookupError(f"No role {role_name!r}")
    return found


async def assign_role(db: AsyncSession, user: User, role_name: str) -> None:
    db.add(
        UserPropertyRole(
            user_id=user.id,
            property_id=await get_default_property_id(db),
            role_id=await role_id(db, role_name),
        )
    )
    await db.flush()


type MakeUser = Callable[..., Awaitable[User]]


@pytest.fixture
def make_user(db_session: AsyncSession) -> MakeUser:
    """Create a user with a role at the default property.

    The password is "<username>-password" unless given; `role=None` creates a user
    without any role.
    """

    async def make(
        username: str = "frontdesk",
        *,
        password: str | None = None,
        role: str | None = Roles.RECEPTIONIST,
        deactivated: bool = False,
        expires_at: datetime | None = None,
    ) -> User:
        user = User(
            username=username,
            password_hash=cached_password_hash(password or f"{username}-password"),
            full_name=username.title(),
            deactivated_at=datetime.now(UTC) if deactivated else None,
            expires_at=expires_at,
        )
        db_session.add(user)
        await db_session.flush()
        if role is not None:
            await assign_role(db_session, user, role)
        return user

    return make


type LoginAs = Callable[[str], Awaitable[User]]


@pytest.fixture
def login_as(client: AsyncClient, make_user: MakeUser) -> LoginAs:
    """Create a user with the given role (see Roles) and log the test client in."""

    async def login(role: str) -> User:
        username = USERNAME_FOR_ROLE[role]
        user = await make_user(username, role=role)
        response = await client.post(
            "/api/auth/login", json={"username": username, "password": f"{username}-password"}
        )
        assert response.status_code == 200, response.text
        return user

    return login


# --- committed-data fixtures (for WebSocket tests) ---------------------------------
#
# The fixtures above share one rolled-back transaction, which works because each
# HTTP request finishes before the next starts. A WebSocket stays open while REST
# requests run, and opens its own database sessions, so those tests need data that
# is really committed. These fixtures provide that and empty the tables afterwards.

# Properties, roles and permissions come from the migration and are kept.
ALL_TABLES: str = (
    "audit_events, sessions, user_property_roles, reservations, guests, rooms, room_types, users"
)


@pytest.fixture
async def live_db(
    test_settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory on the test database whose commits are real; tables are truncated."""
    engine = create_engine(test_settings.database_url)
    factory = create_session_factory(engine)

    async def truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))

    await truncate()
    try:
        yield factory
    finally:
        await truncate()
        await engine.dispose()


type LiveClientFactory = Callable[..., Awaitable[tuple[FastAPI, AsyncClient]]]


@asynccontextmanager
async def live_clients(settings: Settings) -> AsyncIterator[LiveClientFactory]:
    """Build apps using real DB connections (no session override) plus clients that
    speak both HTTP and WebSocket. `make(**overrides)` creates a new app with those
    settings overridden; `make(app)` adds another client to an existing app.

    A context manager rather than a fixture: the WebSocket transport holds an anyio
    task group, which must be exited by the task that entered it, and pytest-asyncio
    runs fixture setup and teardown in different tasks.
    """
    async with AsyncExitStack() as stack:

        async def make(
            app: FastAPI | None = None, **overrides: object
        ) -> tuple[FastAPI, AsyncClient]:
            if app is None:
                app = create_app(settings.model_copy(update=overrides))
                await stack.enter_async_context(app.router.lifespan_context(app))
            # The client enters and exits its transport itself.
            client = await stack.enter_async_context(
                AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url="https://test")
            )
            return app, client

        yield make
