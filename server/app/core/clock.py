"""Business-date helpers. The hotel's "today" depends on its time zone, not the server's."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def hotel_today(tz: ZoneInfo, now: datetime | None = None) -> date:
    """The current date at the hotel. `now` must be timezone-aware (defaults to the real time)."""
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(tz).date()
