"""Check the reservation data for oversold nights, room clashes and price mismatches.

Usage (from server/):
    uv run python -m app.scripts.check_reservations

Exits 0 when everything is consistent, 1 otherwise (so it can run from cron or CI).
See services/consistency.py for what is checked.
"""

import asyncio
import sys

from app.core.clock import hotel_today
from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.services.consistency import check_consistency
from app.services.permissions import get_default_property_id


async def _main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as db:
            property_id = await get_default_property_id(db)
            report = await check_consistency(db, property_id, hotel_today(settings.hotel_tz))
    finally:
        await engine.dispose()

    print(f"Checked from {report.today.isoformat()}: {report.nights_checked} nights.")
    for o in report.oversold:
        print(f"  OVERSOLD {o.room_type} on {o.night.isoformat()}: {o.over_by} too many")
    for c in report.room_clashes:
        print(f"  ROOM {c.room_number}: {c.first} and {c.second} ({c.reason})")
    for p in report.price_problems:
        print(f"  PRICE {p.confirmation_code}: {p.problem}")
    if report.ok:
        print("Everything is consistent.")
        return 0
    return 1


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
