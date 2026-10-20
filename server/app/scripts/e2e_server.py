"""A backend for the browser tests, on a database of its own (never the dev database).

Usage (from server/; Playwright starts it, see client/playwright.config.ts):
    uv run python -m app.scripts.e2e_server

Takes DATABASE_URL's database name plus "_e2e" (e.g. hotel -> hotel_e2e), creates it if
missing, migrates it, wipes and seeds it with the dev data, then serves the API on
127.0.0.1:8001 for the client origin in E2E_CLIENT_ORIGIN (default
http://localhost:5174). Whatever the tests do stays in that database and is replaced on
the next run.
"""

import asyncio
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.clock import hotel_today
from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.models import Room
from app.scripts.seed import APP_TABLES, seed_dev_data

SERVER_DIR = Path(__file__).resolve().parents[2]
PORT = 8001


def e2e_database_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(database=f"{url.database}_e2e").render_as_string(hide_password=False)


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
                quoted = conn.dialect.identifier_preparer.quote(str(url.database))
                await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await engine.dispose()


async def _reset_and_seed(database_url: str, timezone: ZoneInfo) -> int:
    engine = create_engine(database_url)
    try:
        async with create_session_factory(engine)() as session:
            await session.execute(
                text(f"TRUNCATE {', '.join(APP_TABLES)} RESTART IDENTITY CASCADE")
            )
            await seed_dev_data(session, hotel_today(timezone))
            await session.commit()
            return await session.scalar(select(func.count()).select_from(Room)) or 0
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    if settings.environment == "production":
        print("Refusing to run: ENVIRONMENT is production.", file=sys.stderr)
        sys.exit(1)
    database_url = e2e_database_url(settings.database_url)
    if make_url(database_url).database == make_url(settings.database_url).database:
        sys.exit("The e2e database must differ from the dev database.")
    origin = os.environ.get("E2E_CLIENT_ORIGIN", "http://localhost:5174")

    asyncio.run(_create_database_if_missing(database_url))
    config = Config(toml_file=str(SERVER_DIR / "pyproject.toml"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    rooms = asyncio.run(_reset_and_seed(database_url, settings.hotel_tz))
    print(f"e2e database {make_url(database_url).database} seeded ({rooms} rooms)", flush=True)

    app = create_app(
        settings.model_copy(
            update={
                "database_url": database_url,
                "cors_origins": [origin],
                "app_base_url": origin,
                "session_cookie_secure": False,
            }
        )
    )
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
