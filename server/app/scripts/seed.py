"""Seed the development database with a realistic front desk "today".

Usage (from server/):
    uv run python -m app.scripts.seed           # refuses if data already exists
    uv run python -m app.scripts.seed --reset   # wipes all app tables first

Creates 3 room types with base rates, 40 rooms on 4 floors, one user per role (dev
passwords printed at the end) and reservations dated relative to today: the dashboard
shows vacant, occupied, arriving, departing and out-of-order rooms, and around it there
are past stays, cancellations, no-shows and bookings for the coming months. Nothing is
oversold: every booking is checked against the rooms of its type, night by night.
Output is deterministic for a given date.
"""

import argparse
import asyncio
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import hotel_today
from app.core.config import get_settings
from app.core.confirmation_codes import ALPHABET as CODE_ALPHABET
from app.core.confirmation_codes import LENGTH as CODE_LENGTH
from app.core.security import hash_password
from app.db.session import create_engine, create_session_factory
from app.models import (
    Guest,
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationNight,
    ReservationSource,
    ReservationStatus,
    Role,
    Room,
    RoomType,
    User,
    UserPropertyRole,
)
from app.services.permissions import get_default_property_id

# Development-only credentials. The script refuses to run in production.
# One user per default system role: (username, full name, role name).
DEV_USERS: list[tuple[str, str, str]] = [
    ("admin", "Alex Admin", "Admin"),
    ("manager", "Morgan Manager", "General manager"),
    ("fomanager", "Frankie Office", "Front office manager"),
    ("frontdesk", "Fran Desk", "Receptionist"),
    ("housekeeping", "Harper Keeping", "Housekeeping supervisor"),
    ("attendant", "Ari Attendant", "Room attendant"),
]
# Who takes bookings and cancellations.
BOOKING_STAFF: tuple[str, ...] = ("frontdesk", "frontdesk", "fomanager", "manager")


def dev_password(username: str) -> str:
    return f"{username}-dev-pass"


# (code, name, max occupancy, base rate per night in minor units)
ROOM_TYPES: list[tuple[str, str, int, int]] = [
    ("STD", "Standard Queen", 2, 129_900),
    ("DLX", "Deluxe King", 3, 179_900),
    ("STE", "Junior Suite", 4, 249_900),
]
BED_SETUPS: dict[str, str] = {
    "STD": "1 queen or 2 singles",
    "DLX": "1 king",
    "STE": "1 king and a sofa bed",
}

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
GUEST_NOTES: list[str] = [
    "Prefers a high floor.",
    "Regular guest; likes the corner rooms.",
    "Travels with a small dog.",
]
CANCELLATION_REASONS: list[str] = [
    "Change of travel plans.",
    "Booked elsewhere.",
    "Illness.",
    "Flight cancelled.",
]
SOURCES: list[ReservationSource] = [
    ReservationSource.PHONE,
    ReservationSource.EMAIL,
    ReservationSource.OTHER,
]
SOURCE_WEIGHTS: list[int] = [40, 45, 15]
# Share of bookings at a negotiated rate below the room type's base rate.
CORPORATE_RATE_SHARE: float = 0.1


@dataclass
class SeedSummary:
    users: int = 0
    room_types: int = 0
    rooms: int = 0
    guests: int = 0
    reservations: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    scenario_rooms: dict[str, list[str]] = field(default_factory=dict)
    unassigned_arrivals: int = 0
    future_bookings: int = 0


def _nights(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


class _Builder:
    """Creates guests and reservations with deterministic random details, keeping
    track of inventory so nothing is oversold and no room is double-booked."""

    def __init__(
        self,
        session: AsyncSession,
        rng: random.Random,
        today: date,
        prop: Property,
        staff: dict[str, User],
        rooms: list[Room],
    ) -> None:
        self.session = session
        self.rng = rng
        self.today = today
        self.property_id = prop.id
        self.currency = prop.currency
        self.tz = ZoneInfo(prop.time_zone)
        self.now = datetime.now(UTC)
        self.staff = staff
        self.rooms = rooms
        self.codes: set[str] = set()
        self.guests: list[Guest] = []
        self.returning: list[Guest] = []  # guests with a past stay
        self.statuses: Counter[ReservationStatus] = Counter()
        # Sellable rooms per type; out-of-order rooms are not sellable tonight only.
        self.capacity: Counter[int] = Counter()
        self.out_of_order_tonight: Counter[int] = Counter()
        # Active (confirmed or in-house) reservations per (type, night).
        self.booked: Counter[tuple[int, date]] = Counter()
        # Nights each room is taken, by any stay with a room (past ones included).
        self.room_nights: dict[str, set[date]] = {r.number: set() for r in rooms}

    def count_inventory(self) -> None:
        """Call once the rooms' housekeeping statuses are set."""
        for room in self.rooms:
            self.capacity[room.room_type.id] += 1
            if room.housekeeping_status is HousekeepingStatus.OUT_OF_ORDER:
                self.out_of_order_tonight[room.room_type.id] += 1

    # --- inventory --------------------------------------------------------------------

    def fits(self, room_type: RoomType, check_in: date, check_out: date) -> bool:
        return all(
            self.booked[(room_type.id, night)] < self._sellable(room_type, night)
            for night in _nights(check_in, check_out)
        )

    def _sellable(self, room_type: RoomType, night: date) -> int:
        out_of_order = self.out_of_order_tonight[room_type.id] if night == self.today else 0
        return self.capacity[room_type.id] - out_of_order

    def free_room(self, room_type: RoomType, check_in: date, check_out: date) -> Room | None:
        nights = set(_nights(check_in, check_out))
        candidates = [
            r
            for r in self.rooms
            if r.room_type is room_type and not nights & self.room_nights[r.number]
        ]
        return self.rng.choice(candidates) if candidates else None

    # --- records ----------------------------------------------------------------------

    def _guest(self, *, returning: bool = False) -> Guest:
        if returning and self.returning:
            return self.rng.choice(self.returning)
        first = self.rng.choice(FIRST_NAMES)
        last = self.rng.choice(LAST_NAMES)
        n = len(self.guests) + 1
        guest = Guest(
            property_id=self.property_id,
            first_name=first,
            last_name=last,
            # Some guests book without an email address or a phone number.
            email=f"{first}.{last}{n}@example.com".lower() if self.rng.random() < 0.85 else None,
            # 555-01xx numbers are reserved for fiction.
            phone=f"+1-555-01{n % 100:02d}" if self.rng.random() < 0.75 else None,
            notes=self.rng.choice(GUEST_NOTES) if self.rng.random() < 0.08 else None,
        )
        self.guests.append(guest)
        return guest

    def _code(self) -> str:
        while True:
            code = "".join(self.rng.choices(CODE_ALPHABET, k=CODE_LENGTH))
            if code not in self.codes:
                self.codes.add(code)
                return code

    def _at(self, day: date, hour: int, minute: int = 0) -> datetime:
        """A moment in hotel time, never later than now."""
        return min(datetime.combine(day, time(hour, minute), self.tz), self.now)

    def _staff(self) -> User:
        return self.staff[self.rng.choice(BOOKING_STAFF)]

    def reservation(
        self,
        room_type: RoomType,
        *,
        check_in: int,
        check_out: int,
        status: ReservationStatus,
        room: Room | None = None,
        vip: bool | None = None,
        walk_in: bool = False,
        returning_guest: bool = False,
    ) -> Reservation:
        """check_in/check_out are day offsets from today."""
        rng = self.rng
        check_in_date = self.today + timedelta(days=check_in)
        check_out_date = self.today + timedelta(days=check_out)
        nights = _nights(check_in_date, check_out_date)
        rate = room_type.base_rate_minor
        if rng.random() < CORPORATE_RATE_SHARE:
            rate = rate * 9 // 10 // 100 * 100  # 10% off, whole currency units
        adults = rng.randint(1, min(2, room_type.max_occupancy))
        children = rng.randint(0, room_type.max_occupancy - adults)

        source = ReservationSource.WALK_IN if walk_in else rng.choices(SOURCES, SOURCE_WEIGHTS)[0]
        lead_days = 0 if walk_in else rng.choice([0, 1, 3, 7, 14, 21, 30, 45, 60])
        booked_on = check_in_date - timedelta(days=lead_days)
        if booked_on > self.today:  # bookings for later dates were made already
            booked_on = self.today - timedelta(days=rng.randint(0, 30))
        created_at = self._at(
            booked_on,
            rng.randint(16, 21) if walk_in else rng.randint(8, 20),
            rng.randint(0, 59),
        )
        reservation = Reservation(
            property_id=self.property_id,
            confirmation_code=self._code(),
            guest=self._guest(returning=returning_guest),
            room_type=room_type,
            room=room,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            adults=adults,
            children=children,
            status=status,
            source=source,
            is_vip=rng.random() < 0.15 if vip is None else vip,
            notes=rng.choice(NOTES) if rng.random() < 0.3 else None,
            nights=[ReservationNight(night_date=night, rate_minor=rate) for night in nights],
            total_minor=rate * len(nights),
            currency=self.currency,
            # Bookings that came in through "other" channels were keyed in by nobody.
            created_by=None if source is ReservationSource.OTHER else self._staff().id,
            created_at=created_at,
        )
        if status in (ReservationStatus.CHECKED_IN, ReservationStatus.CHECKED_OUT):
            reservation.checked_in_at = max(
                created_at, self._at(check_in_date, rng.randint(14, 22), rng.randint(0, 59))
            )
        if status is ReservationStatus.CHECKED_OUT:
            reservation.checked_out_at = max(
                reservation.checked_in_at or created_at,
                self._at(check_out_date, rng.randint(7, 11), rng.randint(0, 59)),
            )
        if status is ReservationStatus.CANCELLED:
            latest = max(created_at, self._at(check_in_date, 12))
            reservation.cancelled_at = created_at + (latest - created_at) * rng.random()
            reservation.cancelled_by = self._staff().id
            reservation.cancellation_reason = rng.choice(CANCELLATION_REASONS)
        if status is ReservationStatus.NO_SHOW:
            # Marked the morning after the arrival day.
            reservation.cancelled_at = max(created_at, self._at(check_in_date + timedelta(1), 2))
            reservation.cancelled_by = self.staff["fomanager"].id
            reservation.cancellation_reason = "Did not arrive."

        if status in (ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN):
            for night in nights:
                self.booked[(room_type.id, night)] += 1
        if room is not None and status is not ReservationStatus.CANCELLED:
            self.room_nights[room.number].update(nights)
        if status is ReservationStatus.CHECKED_OUT:
            self.returning.append(reservation.guest)
        self.statuses[status] += 1
        self.session.add(reservation)
        return reservation


async def seed_dev_data(session: AsyncSession, today: date) -> SeedSummary:
    """Insert the dev dataset and flush (the caller commits or rolls back)."""
    rng = random.Random(today.toordinal())  # noqa: S311 - reproducible dev data, not security
    summary = SeedSummary()

    # The property and its roles come from the migration (system roles of the property).
    property_id = await get_default_property_id(session)
    prop = await session.get_one(Property, property_id)
    roles = {
        role.name: role.id
        for role in (
            await session.scalars(
                select(Role).where(Role.property_id == property_id, Role.is_system)
            )
        ).all()
    }
    users = [
        User(
            username=username,
            password_hash=hash_password(dev_password(username)),
            full_name=full_name,
            job_title=role_name,
        )
        for username, full_name, role_name in DEV_USERS
    ]
    session.add_all(users)
    await session.flush()
    session.add_all(
        UserPropertyRole(user_id=user.id, property_id=property_id, role_id=roles[role_name])
        for user, (_, _, role_name) in zip(users, DEV_USERS, strict=True)
    )
    summary.users = len(DEV_USERS)

    types = {
        code: RoomType(
            property_id=property_id,
            code=code,
            name=name,
            max_occupancy=occupancy,
            bed_setup=BED_SETUPS[code],
            base_rate_minor=rate,
        )
        for code, name, occupancy, rate in ROOM_TYPES
    }
    session.add_all(types.values())
    await session.flush()  # ids for the inventory counters
    summary.room_types = len(types)

    rooms: list[Room] = []
    for floor in FLOORS:
        for n in range(1, ROOMS_PER_FLOOR + 1):
            code = "STD" if n <= 6 else "DLX" if n <= 9 else "STE"
            rooms.append(
                Room(
                    property_id=property_id,
                    number=f"{floor}{n:02d}",
                    floor=floor,
                    room_type=types[code],
                )
            )
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

    hk = HousekeepingStatus
    status = ReservationStatus
    for room in by_scenario["out_of_order"]:
        room.housekeeping_status = hk.OUT_OF_ORDER
    for room in by_scenario["stayover"]:
        room.housekeeping_status = rng.choice([hk.DIRTY, hk.IN_PROGRESS, hk.CLEAN])
    for room in by_scenario["departing"]:
        room.housekeeping_status = rng.choice([hk.DIRTY, hk.CLEAN])
    for room in by_scenario["turnover"]:
        room.housekeeping_status = hk.DIRTY
    for room in by_scenario["departed"]:
        room.housekeeping_status = rng.choice([hk.DIRTY, hk.IN_PROGRESS])
    for room in by_scenario["arriving"]:
        room.housekeeping_status = rng.choice([hk.CLEAN, hk.INSPECTED])
    for room in by_scenario["vacant"]:
        room.housekeeping_status = rng.choice([hk.CLEAN, hk.INSPECTED, hk.INSPECTED, hk.DIRTY])

    staff = {user.username: user for user in users}
    b = _Builder(session, rng, today, prop, staff, rooms)
    b.count_inventory()

    # --- today: one stay per scenario room (always fits: one party per room) ----------
    for i, room in enumerate(by_scenario["stayover"]):
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 4),
            check_out=rng.randint(1, 4),
            status=status.CHECKED_IN,
            room=room,
            vip=True if i == 0 else None,
        )
    for room in by_scenario["departing"]:
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 5),
            check_out=0,
            status=status.CHECKED_IN,
            room=room,
        )
    for room in by_scenario["turnover"]:
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
        b.reservation(
            room.room_type,
            check_in=-rng.randint(1, 4),
            check_out=0,
            status=status.CHECKED_OUT,
            room=room,
        )
    for i, room in enumerate(by_scenario["arriving"]):
        b.reservation(
            room.room_type,
            check_in=0,
            check_out=rng.randint(1, 5),
            status=status.CONFIRMED,
            room=room,
            vip=True if i == 0 else None,
        )

    # Arrivals today still waiting for a room assignment (one of them a walk-in).
    unassigned = 3
    for i in range(unassigned):
        nights = rng.randint(1, 3)
        room_type = next(
            t
            for t in rng.sample(list(types.values()), k=len(types))
            if b.fits(t, today, today + timedelta(days=nights))
        )
        b.reservation(
            room_type, check_in=0, check_out=nights, status=status.CONFIRMED, walk_in=i == 0
        )
    summary.unassigned_arrivals = unassigned

    # --- the past six weeks: stays in rooms, cancellations, no-shows ------------------
    type_list = list(types.values())
    type_weights = [b.capacity[t.id] for t in type_list]
    for _ in range(90):
        room_type = rng.choices(type_list, type_weights)[0]
        nights = rng.choice([1, 1, 2, 2, 3, 4, 5, 7])
        check_out = -rng.randint(1, 42)
        past_room = b.free_room(
            room_type, today + timedelta(days=check_out - nights), today + timedelta(check_out)
        )
        if past_room is not None:
            b.reservation(
                room_type,
                check_in=check_out - nights,
                check_out=check_out,
                status=status.CHECKED_OUT,
                room=past_room,
            )
    for _ in range(8):
        check_in = -rng.randint(1, 40)
        b.reservation(
            rng.choice(type_list),
            check_in=check_in,
            check_out=check_in + rng.randint(1, 4),
            status=status.CANCELLED,
        )
    for _ in range(3):
        check_in = -rng.randint(2, 30)
        b.reservation(
            rng.choice(type_list),
            check_in=check_in,
            check_out=check_in + rng.randint(1, 3),
            status=status.NO_SHOW,
        )
    # Today's dashboard ignores these.
    b.reservation(types["STD"], check_in=0, check_out=2, status=status.CANCELLED)
    b.reservation(types["DLX"], check_in=-1, check_out=1, status=status.NO_SHOW)

    # --- the coming three months: bookings without a room yet, a few cancelled --------
    future = 0
    for _ in range(140):
        room_type = rng.choices(type_list, type_weights)[0]
        # More bookings for the near future than for later.
        check_in = 1 + int(rng.triangular(0, 90, 0))
        check_out = check_in + rng.choice([1, 1, 2, 2, 2, 3, 3, 4, 5, 7, 10])
        if b.fits(room_type, today + timedelta(check_in), today + timedelta(check_out)):
            b.reservation(
                room_type,
                check_in=check_in,
                check_out=check_out,
                status=status.CONFIRMED,
                returning_guest=rng.random() < 0.3,
            )
            future += 1
    for _ in range(6):
        check_in = rng.randint(3, 60)
        b.reservation(
            rng.choice(type_list),
            check_in=check_in,
            check_out=check_in + rng.randint(1, 4),
            status=status.CANCELLED,
        )

    await session.flush()

    summary.guests = len(b.guests)
    summary.reservations = b.statuses.total()
    summary.by_status = {s.value: b.statuses[s] for s in ReservationStatus}
    summary.future_bookings = future
    summary.scenario_rooms = {name: [r.number for r in rs] for name, rs in by_scenario.items()}
    return summary


# Wiped by --reset. Properties, roles and permissions come from the migration (and
# the startup sync) and are kept.
APP_TABLES: tuple[str, ...] = (
    "audit_events",
    "sessions",
    "user_property_roles",
    "idempotency_keys",
    "reservation_nights",
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
    print("  " + ", ".join(f"{count} {name}" for name, count in summary.by_status.items()))
    for name, numbers in summary.scenario_rooms.items():
        print(f"  {name:<13}{len(numbers):>3}  {' '.join(numbers)}")
    print(f"  {'unassigned arrivals':<17}{summary.unassigned_arrivals:>3}")
    print(f"  {'future bookings':<17}{summary.future_bookings:>3}")
    print("\nDev logins (username / password):")
    for username, _, role_name in DEV_USERS:
        print(f"  {role_name:<24} {username} / {dev_password(username)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--reset", action="store_true", help="truncate all app tables first")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(reset=args.reset)))


if __name__ == "__main__":
    main()
