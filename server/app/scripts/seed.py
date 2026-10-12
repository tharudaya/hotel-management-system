"""Seed the development database with a realistic front desk "today".

Usage (from server/):
    uv run python -m app.scripts.seed           # refuses if data already exists
    uv run python -m app.scripts.seed --reset   # wipes all app tables first

Creates 3 room types, 40 rooms on 4 floors, one user per role (dev passwords
printed at the end) and reservations dated relative to today, so the
dashboard shows vacant, occupied, arriving, departing and out-of-order rooms.
Output is deterministic for a given date.
"""

import argparse
import asyncio
import random
import string
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import hotel_today
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import create_engine, create_session_factory
from app.models import (
    Guest,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
    User,
    UserRole,
)

# Development-only credentials. The script refuses to run in production.
DEV_USERS: list[tuple[str, str, UserRole]] = [
    ("frontdesk", "Fran Desk", UserRole.FRONT_DESK),
    ("housekeeping", "Harper Keeping", UserRole.HOUSEKEEPING),
    ("manager", "Morgan Manager", UserRole.MANAGER),
    ("admin", "Alex Admin", UserRole.ADMIN),
]


def dev_password(username: str) -> str:
    return f"{username}-dev-pass"


ROOM_TYPES: list[tuple[str, str, int]] = [
    ("STD", "Standard Queen", 2),
    ("DLX", "Deluxe King", 3),
    ("STE", "Junior Suite", 4),
]

FLOORS: range = range(1, 5)
ROOMS_PER_FLOOR: int = 10

FIRST_NAMES: list[str] = [
    "Ava", "Ben", "Chloe", "Daniel", "Elena", "Farah", "George", "Hana", "Isaac", "Julia",
    "Kenji", "Lena", "Mateo", "Nora", "Omar", "Priya", "Quinn", "Rosa", "Samuel", "Tara",
    "Umar", "Vera", "Wei", "Ximena", "Yusuf", "Zoe",
]  # fmt: skip
LAST_NAMES: list[str] = [
    "Andersen", "Bauer", "Costa", "Dubois", "Evans", "Fischer", "Garcia", "Hughes", "Ito",
    "Jensen", "Kowalski", "Lopez", "Moreau", "Nakamura", "Okafor", "Patel", "Rossi", "Silva",
    "Tanaka", "Usman", "Varga", "Weber", "Yilmaz", "Zhang",
]  # fmt: skip
NOTES: list[str] = [
    "Late arrival, around 11pm.",
    "Requests extra pillows.",
    "Celebrating an anniversary.",
    "Allergic to feathers; hypoallergenic bedding please.",
    "Quiet room away from the elevator.",
    "Early check-in requested if available.",
]

# Unambiguous characters only (no 0/O, 1/I).
CODE_ALPHABET: str = "".join(c for c in string.ascii_uppercase + string.digits if c not in "01IO")


@dataclass
class SeedSummary:
    users: int = 0
    room_types: int = 0
    rooms: int = 0
    guests: int = 0
    reservations: int = 0
    scenario_rooms: dict[str, list[str]] = field(default_factory=dict)
    unassigned_arrivals: int = 0


class _Builder:
    """Creates guests and reservations with deterministic random details."""

    def __init__(self, session: AsyncSession, rng: random.Random, today: date) -> None:
        self.session = session
        self.rng = rng
        self.today = today
        self.codes: set[str] = set()
        self.guests = 0
        self.reservations = 0

    def _guest(self) -> Guest:
        first = self.rng.choice(FIRST_NAMES)
        last = self.rng.choice(LAST_NAMES)
        self.guests += 1
        return Guest(
            first_name=first,
            last_name=last,
            email=f"{first}.{last}{self.guests}@example.com".lower(),
            # 555-01xx numbers are reserved for fiction.
            phone=f"+1-555-01{self.guests % 100:02d}",
        )

    def _code(self) -> str:
        while True:
            code = "".join(self.rng.choices(CODE_ALPHABET, k=8))
            if code not in self.codes:
                self.codes.add(code)
                return code

    def reservation(
        self,
        room_type: RoomType,
        *,
        check_in: int,
        check_out: int,
        status: ReservationStatus,
        room: Room | None = None,
        vip: bool | None = None,
    ) -> Reservation:
        """check_in/check_out are day offsets from today."""
        adults = self.rng.randint(1, min(2, room_type.max_occupancy))
        children = self.rng.randint(0, room_type.max_occupancy - adults)
        reservation = Reservation(
            confirmation_code=self._code(),
            guest=self._guest(),
            room_type=room_type,
            room=room,
            check_in_date=self.today + timedelta(days=check_in),
            check_out_date=self.today + timedelta(days=check_out),
            adults=adults,
            children=children,
            status=status,
            is_vip=self.rng.random() < 0.15 if vip is None else vip,
            notes=self.rng.choice(NOTES) if self.rng.random() < 0.3 else None,
        )
        self.session.add(reservation)
        self.reservations += 1
        return reservation


async def seed_dev_data(session: AsyncSession, today: date) -> SeedSummary:
    """Insert the dev dataset and flush (the caller commits or rolls back)."""
    rng = random.Random(today.toordinal())  # noqa: S311 - reproducible dev data, not security
    summary = SeedSummary()

    for username, full_name, role in DEV_USERS:
        session.add(
            User(
                username=username,
                password_hash=hash_password(dev_password(username)),
                full_name=full_name,
                role=role,
            )
        )
    summary.users = len(DEV_USERS)

    types = {
        code: RoomType(code=code, name=name, max_occupancy=occupancy)
        for code, name, occupancy in ROOM_TYPES
    }
    session.add_all(types.values())
    summary.room_types = len(types)

    rooms: list[Room] = []
    for floor in FLOORS:
        for n in range(1, ROOMS_PER_FLOOR + 1):
            code = "STD" if n <= 6 else "DLX" if n <= 9 else "STE"
            rooms.append(Room(number=f"{floor}{n:02d}", floor=floor, room_type=types[code]))
    session.add_all(rooms)
    summary.rooms = len(rooms)

    # Assign each room a scenario for today. Counts sum to 40.
    scenarios: list[tuple[str, int]] = [
        ("out_of_order", 2),
        ("stayover", 12),  # in house, leaving a later day
        ("departing", 6),  # in house, leaving today
        ("turnover", 2),  # departing today and someone else arriving today
        ("departed", 3),  # already checked out today
        ("arriving", 6),  # arriving today, room pre-assigned
        ("vacant", 0),  # everything left over
    ]
    shuffled = rooms.copy()
    rng.shuffle(shuffled)
    by_scenario: dict[str, list[Room]] = {}
    start = 0
    for name, count in scenarios:
        end = len(shuffled) if name == "vacant" else start + count
        by_scenario[name] = sorted(shuffled[start:end], key=lambda r: r.number)
        start = end

    b = _Builder(session, rng, today)
    hk = HousekeepingStatus
    status = ReservationStatus

    for room in by_scenario["out_of_order"]:
        room.housekeeping_status = hk.OUT_OF_ORDER

    for i, room in enumerate(by_scenario["stayover"]):
        room.housekeeping_status = rng.choice([hk.DIRTY, hk.IN_PROGRESS, hk.CLEAN])
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 4),
            check_out=rng.randint(1, 4),
            status=status.CHECKED_IN,
            room=room,
            vip=True if i == 0 else None,
        )

    for room in by_scenario["departing"]:
        room.housekeeping_status = rng.choice([hk.DIRTY, hk.CLEAN])
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 5),
            check_out=0,
            status=status.CHECKED_IN,
            room=room,
        )

    for room in by_scenario["turnover"]:
        room.housekeeping_status = hk.DIRTY
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 3),
            check_out=0,
            status=status.CHECKED_IN,
            room=room,
        )
        b.reservation(
            room.room_type,
            check_in=0,
            check_out=rng.randint(1, 4),
            status=status.CONFIRMED,
            room=room,
        )

    for room in by_scenario["departed"]:
        room.housekeeping_status = rng.choice([hk.DIRTY, hk.IN_PROGRESS])
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 4),
            check_out=0,
            status=status.CHECKED_OUT,
            room=room,
        )

    for i, room in enumerate(by_scenario["arriving"]):
        room.housekeeping_status = rng.choice([hk.CLEAN, hk.INSPECTED])
        b.reservation(
            room.room_type,
            check_in=0,
            check_out=rng.randint(1, 5),
            status=status.CONFIRMED,
            room=room,
            vip=True if i == 0 else None,
        )

    for room in by_scenario["vacant"]:
        room.housekeeping_status = rng.choice([hk.CLEAN, hk.INSPECTED, hk.INSPECTED, hk.DIRTY])

    # Arrivals today still waiting for a room assignment.
    unassigned = 3
    for _ in range(unassigned):
        b.reservation(
            types[rng.choice(list(types))],
            check_in=0,
            check_out=rng.randint(1, 3),
            status=status.CONFIRMED,
        )
    summary.unassigned_arrivals = unassigned

    # Background noise the dashboard should ignore.
    b.reservation(types["STD"], check_in=0, check_out=2, status=status.CANCELLED)
    b.reservation(types["DLX"], check_in=-1, check_out=1, status=status.NO_SHOW)
    for _ in range(4):
        b.reservation(
            types[rng.choice(list(types))],
            check_in=rng.randint(2, 10),
            check_out=rng.randint(11, 14),
            status=status.CONFIRMED,
        )

    await session.flush()

    summary.guests = b.guests
    summary.reservations = b.reservations
    summary.scenario_rooms = {name: [r.number for r in rs] for name, rs in by_scenario.items()}
    return summary


APP_TABLES: tuple[str, ...] = (
    "audit_events",
    "sessions",
    "reservations",
    "guests",
    "rooms",
    "room_types",
    "users",
)


async def _main(reset: bool) -> int:
    settings = get_settings()
    if settings.environment == "production":
        print("Refusing to seed: ENVIRONMENT is production.", file=sys.stderr)
        return 1

    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            if reset:
                await session.execute(
                    text(f"TRUNCATE {', '.join(APP_TABLES)} RESTART IDENTITY CASCADE")
                )
            elif await session.scalar(select(func.count()).select_from(Room)):
                print("Database already has data; rerun with --reset to wipe it.", file=sys.stderr)
                return 1

            today = hotel_today(settings.hotel_tz)
            summary = await seed_dev_data(session, today)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"Seeded for {today.isoformat()}:")
    print(
        f"  {summary.users} users, {summary.room_types} room types, {summary.rooms} rooms, "
        f"{summary.guests} guests, {summary.reservations} reservations"
    )
    for name, numbers in summary.scenario_rooms.items():
        print(f"  {name:<13}{len(numbers):>3}  {' '.join(numbers)}")
    print(f"  {'unassigned arrivals':<17}{summary.unassigned_arrivals:>3}")
    print("\nDev logins (username / password):")
    for username, _, role in DEV_USERS:
        print(f"  {role.value:<13} {username} / {dev_password(username)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--reset", action="store_true", help="truncate all app tables first")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(reset=args.reset)))


if __name__ == "__main__":
    main()
