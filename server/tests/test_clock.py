from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.clock import hotel_today

STOCKHOLM: ZoneInfo = ZoneInfo("Europe/Stockholm")


@pytest.mark.parametrize(
    ("utc_now", "expected"),
    [
        # Summer (CEST, UTC+2): 22:30 UTC is already tomorrow in Stockholm.
        (datetime(2026, 10, 12, 21, 59, tzinfo=UTC), date(2026, 10, 12)),
        (datetime(2026, 10, 12, 22, 30, tzinfo=UTC), date(2026, 10, 13)),
        # Winter (CET, UTC+1).
        (datetime(2026, 1, 15, 22, 59, tzinfo=UTC), date(2026, 1, 15)),
        (datetime(2026, 1, 15, 23, 0, tzinfo=UTC), date(2026, 1, 16)),
    ],
)
def test_hotel_today_uses_hotel_time_zone(utc_now: datetime, expected: date) -> None:
    assert hotel_today(STOCKHOLM, utc_now) == expected


def test_hotel_today_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        hotel_today(STOCKHOLM, datetime(2026, 1, 15, 12, 0))


def test_hotel_today_defaults_to_now() -> None:
    assert hotel_today(STOCKHOLM) == datetime.now(STOCKHOLM).date()
