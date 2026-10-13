"""Run the expired-users sweep once (for cron, if the in-process sweep is disabled).

    python -m app.scripts.expire_users

The API process also runs it every EXPIRY_SWEEP_INTERVAL_SECONDS; running both is
safe (an advisory lock lets one sweep at a time). WebSocket closing only reaches
sockets on this process's hub, i.e. none: from cron, open sockets close at their
next heartbeat instead (sessions are revoked either way).
"""

import asyncio

from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.realtime.bus import EventBus
from app.services.expiry import sweep_expired_users


async def main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as db:
            result = await sweep_expired_users(db, EventBus())
    finally:
        await engine.dispose()
    if not result.ran:
        print("Another process is sweeping right now; nothing done.")
        return 0
    print(
        f"Expired users processed: {len(result.expired_user_ids)}; "
        f"sessions revoked: {result.sessions_revoked}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
