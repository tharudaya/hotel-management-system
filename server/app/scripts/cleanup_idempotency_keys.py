"""Delete idempotency keys older than 24 hours.

The API process does this on its own every EXPIRY_SWEEP_INTERVAL_SECONDS; this command
is for running it from cron instead (or as well).

Usage (from server/):
    uv run python -m app.scripts.cleanup_idempotency_keys
"""

import asyncio

from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.services.idempotency import delete_expired


async def _main() -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as db:
            deleted = await delete_expired(db)
    finally:
        await engine.dispose()
    print(f"Deleted {deleted} idempotency key(s) older than 24 hours.")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
