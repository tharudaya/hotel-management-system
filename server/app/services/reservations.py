"""Reservations: details, creation (ReservationService), check-in and check-out.

See "Reservations" in CLAUDE.md for the rules. Creating a booking runs in one
transaction: idempotency check, stay validation, room type row lock, availability
recount under the lock, guest, reservation with its nights, audit row, idempotency
record; events are published after commit.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.confirmation_codes import new_confirmation_code
from app.core.permissions import P
from app.models import (
    AuditAction,
    AuditLog,
    Guest,
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationNight,
    ReservationSource,
    ReservationStatus,
    Room,
    RoomType,
    User,
)
from app.realtime.bus import EventBus
from app.realtime.events import ReservationChanged, SummaryChanged
from app.schemas.availability import NightlyRate
from app.schemas.dashboard import DashboardRoom
from app.schemas.reservations import (
    GuestOut,
    HistoryActor,
    ReservationCancel,
    ReservationCreate,
    ReservationDetail,
    ReservationHistoryEntry,
    ReservationNoShow,
    ReservationUpdate,
    ReservationView,
    WalkInCreate,
)
from app.services import idempotency
from app.services.audit import ClientInfo, record_change
from app.services.availability import ACTIVE_STATUSES, AvailabilityService, lock_room_type
from app.services.dashboard import get_dashboard_summary
from app.services.errors import (
    DomainError,
    InvalidInputError,
    InvalidTransitionError,
    NoAvailabilityError,
    NotFoundError,
    PermissionDeniedError,
    VersionConflictError,
)
from app.services.guests import GuestService
from app.services.room_changes import (
    ensure_version,
    load_room,
    publish_room_changes,
    touch,
)
from app.services.rooms import HOUSEKEEPING_PHRASES
from app.services.stays import stay_nights, validate_stay

# How a reservation's status reads in messages shown to staff.
STATUS_PHRASES: dict[ReservationStatus, str] = {
    ReservationStatus.CONFIRMED: "not checked in yet",
    ReservationStatus.CHECKED_IN: "already checked in",
    ReservationStatus.CHECKED_OUT: "already checked out",
    ReservationStatus.CANCELLED: "cancelled",
    ReservationStatus.NO_SHOW: "marked as a no-show",
}

READY_FOR_GUEST: frozenset[HousekeepingStatus] = frozenset(
    {HousekeepingStatus.CLEAN, HousekeepingStatus.INSPECTED}
)

CREATE_ENDPOINT: str = "POST /api/reservations"
WALK_IN_ENDPOINT: str = "POST /api/reservations/walk-in"
# Codes are random (31**8 of them): a collision is rare, several in a row mean a bug.
CODE_ATTEMPTS: int = 5


async def get_reservation_detail(
    db: AsyncSession, reservation_id: int, *, include_contact: bool, property_id: int | None = None
) -> ReservationDetail:
    stmt = (
        select(Reservation, Guest, RoomType, Room)
        .join(Reservation.guest)
        .join(Reservation.room_type)
        .outerjoin(Reservation.room)
        .where(Reservation.id == reservation_id)
        .execution_options(populate_existing=True)
    )
    if property_id is not None:
        stmt = stmt.where(Reservation.property_id == property_id)
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise NotFoundError(f"Reservation {reservation_id} not found")
    reservation, guest, room_type, room = row
    nights = (
        await db.execute(
            select(ReservationNight.night_date, ReservationNight.rate_minor)
            .where(ReservationNight.reservation_id == reservation.id)
            .order_by(ReservationNight.night_date)
        )
    ).all()
    return ReservationDetail(
        id=reservation.id,
        confirmation_code=reservation.confirmation_code,
        status=reservation.status,
        source=reservation.source,
        check_in_date=reservation.check_in_date,
        check_out_date=reservation.check_out_date,
        nights=(reservation.check_out_date - reservation.check_in_date).days,
        adults=reservation.adults,
        children=reservation.children,
        is_vip=reservation.is_vip,
        notes=reservation.notes,
        room_type_id=room_type.id,
        room_type=room_type.code,
        room_type_name=room_type.name,
        room_id=room.id if room else None,
        room_number=room.number if room else None,
        guest=GuestOut(
            id=guest.id,
            first_name=guest.first_name,
            last_name=guest.last_name,
            # Contact details need guest.view_contact (decided by the caller).
            email=guest.email if include_contact else None,
            phone=guest.phone if include_contact else None,
            contact_hidden=not include_contact,
        ),
        nightly_rates=[NightlyRate(night_date=n, rate_minor=rate) for n, rate in nights],
        total_minor=reservation.total_minor,
        currency=reservation.currency,
        version=reservation.version,
        created_at=reservation.created_at,
    )


def _dates(nights: list[date], limit: int = 5) -> str:
    shown = ", ".join(n.isoformat() for n in nights[:limit])
    more = len(nights) - limit
    return f"{shown} and {more} more" if more > 0 else shown


@dataclass
class ReservationService:
    """Acts on behalf of `actor` at one property. Construct per request."""

    db: AsyncSession
    bus: EventBus
    actor: User
    property_id: int
    client: ClientInfo
    today: date
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        # Read now: a rollback expires loaded objects, the actor included.
        self._actor_id: int = self.actor.id

    @property
    def _include_contact(self) -> bool:
        return P.GUEST_VIEW_CONTACT in self.permissions

    async def create(
        self, data: ReservationCreate, idempotency_key: str
    ) -> tuple[ReservationDetail, bool]:
        """Book a stay. Returns (reservation, replayed); replayed is true when the key was
        used before for the same request, and nothing new was created."""
        body_hash = idempotency.request_hash(data)
        booked = await self._book(data, idempotency_key, body_hash, CREATE_ENDPOINT)
        if isinstance(booked, ReservationDetail):
            return booked, True
        event = self._event(booked, "created")
        await self.db.commit()

        await self._publish(event, set(), touches_today=False)
        return await self._detail(event.reservation_id), False

    async def walk_in(
        self, data: WalkInCreate, idempotency_key: str
    ) -> tuple[ReservationDetail, bool]:
        """Book from today and check in to `room_id`, in one transaction: either the guest
        has a booking and a room, or nothing happened."""
        body_hash = idempotency.request_hash(data)
        replay = await self._replay(idempotency_key, body_hash, WALK_IN_ENDPOINT)
        if replay is not None:
            return replay, True
        room = await load_room(self.db, data.room_id)
        booking = ReservationCreate(
            guest_id=data.guest_id,
            guest=data.guest,
            room_type_id=room.room_type_id,
            check_in_date=self.today,
            check_out_date=data.check_out_date,
            adults=data.adults,
            children=data.children,
            source=ReservationSource.WALK_IN,
            notes=data.notes,
            is_vip=data.is_vip,
            nightly_rate_minor=data.nightly_rate_minor,
        )
        booked = await self._book(booking, idempotency_key, body_hash, WALK_IN_ENDPOINT)
        if isinstance(booked, ReservationDetail):
            return booked, True
        try:
            # Under the room type lock now: read the room again (its type may have moved).
            room = await load_room(self.db, data.room_id)
            ensure_version(room, data.room_version)
            await self._check_in_steps(booked, room)
        except BaseException:
            # The booking (and a new guest) must not outlive a failed check-in.
            await self.db.rollback()
            raise
        event = self._event(booked, "checked_in")
        await self._commit_room_change(booked.confirmation_code, room.number)

        await self._publish(event, {room.id}, touches_today=True)
        return await self._detail(event.reservation_id), False

    async def check_in(self, reservation_id: int, room_id: int, room_version: int) -> DashboardRoom:
        """confirmed -> checked_in, into a ready, vacant room of the reserved type; from the
        arrival day (any time that day) until the stay ends. Returns the room's tile."""
        reservation = await self._load_for_update(reservation_id)
        room = await load_room(self.db, room_id)
        ensure_version(room, room_version)
        previous_room_id = reservation.room_id
        await self._check_in_steps(reservation, room)
        event = self._event(reservation, "checked_in")
        await self._commit_room_change(reservation.confirmation_code, room.number)

        # Moving the guest from a pre-assigned room changes that room's tile too.
        affected = {room_id} | ({previous_room_id} if previous_room_id is not None else set())
        entries = await self._publish(event, affected, touches_today=True)
        return entries[room_id]

    async def check_out(self, reservation_id: int, room_version: int) -> DashboardRoom:
        """checked_in -> checked_out; the room becomes dirty. Leaving before the booked
        departure shortens the stay to today (at least one night) and drops the unused
        nights from the total."""
        reservation = await self._load_for_update(reservation_id)
        if reservation.status is not ReservationStatus.CHECKED_IN or reservation.room_id is None:
            raise InvalidTransitionError(
                f"Cannot check out: the reservation is {STATUS_PHRASES[reservation.status]}."
            )
        room = await load_room(self.db, reservation.room_id)
        ensure_version(room, room_version)
        housekeeping_before = room.housekeeping_status
        before: dict[str, object] = {
            "status": ReservationStatus.CHECKED_IN.value,
            "housekeeping_status": housekeeping_before.value,
        }
        after: dict[str, object] = {
            "status": ReservationStatus.CHECKED_OUT.value,
            "housekeeping_status": HousekeepingStatus.DIRTY.value,
            "room_id": room.id,
            "room_number": room.number,
        }
        leaves_on = max(self.today, reservation.check_in_date + timedelta(days=1))
        if leaves_on < reservation.check_out_date:
            nights = await self._nights(reservation.id)
            kept = {night: rate for night, rate in nights.items() if night < leaves_on}
            before |= {
                "check_out_date": reservation.check_out_date.isoformat(),
                "total_minor": reservation.total_minor,
            }
            await self._write_nights(reservation.id, nights, kept)
            reservation.check_out_date = leaves_on
            reservation.total_minor = sum(kept.values())
            after |= {"check_out_date": leaves_on.isoformat(), "total_minor": sum(kept.values())}

        reservation.status = ReservationStatus.CHECKED_OUT
        reservation.checked_out_at = func.now()
        room.housekeeping_status = HousekeepingStatus.DIRTY
        touch(room)  # bumps the version even if the room was already dirty
        self._audit(AuditAction.RESERVATION_CHECKED_OUT, reservation, before, after)
        event = self._event(reservation, "checked_out")
        await self._commit_room_change(reservation.confirmation_code, room.number)

        entries = await self._publish(event, {room.id}, touches_today=True)
        return entries[room.id]

    async def assign_room(self, reservation_id: int, version: int, room_id: int) -> ReservationView:
        """Give a confirmed booking its room ahead of arrival (or move it to another).

        The room must be active, of the reserved type and free on the stay's nights.
        Its housekeeping status does not matter yet; check-in requires it ready."""
        reservation = await self._load_for_change(reservation_id, version)
        code = reservation.confirmation_code
        if reservation.status is not ReservationStatus.CONFIRMED:
            raise InvalidTransitionError(
                f"Cannot assign a room to {code}: it is {STATUS_PHRASES[reservation.status]}."
            )
        if reservation.check_out_date <= self.today:
            raise DomainError(f"The stay of {code} has already ended.", code="stay_ended")
        room = await load_room(self.db, room_id)
        if reservation.room_id == room.id:
            return await self.view(reservation_id)
        self._ensure_room_type(room, reservation)
        if not await self._room_still_fits(room, reservation.id, _Stay.of(reservation)):
            raise DomainError(
                f"Room {room.number} is taken by another guest on some of these nights.",
                code="room_not_available",
            )
        affected = {room.id}
        before_numbers = await self._room_numbers({reservation.room_id})
        before: dict[str, object] = {
            "room_id": reservation.room_id,
            "room_number": before_numbers.get(reservation.room_id or 0),
        }
        if reservation.room_id is not None:
            previous = await self.db.get_one(Room, reservation.room_id, populate_existing=True)
            touch(previous)
            affected.add(previous.id)
        reservation.room_id = room.id
        touch(room)
        self._audit(
            AuditAction.RESERVATION_ROOM_ASSIGNED,
            reservation,
            before,
            {"room_id": room.id, "room_number": room.number},
        )
        event = self._event(reservation, "room_assigned")
        await self._commit_room_change(code, room.number)
        await self._publish(event, affected, touches_today=False)
        return await self.view(reservation_id)

    async def unassign_room(self, reservation_id: int, version: int) -> ReservationView:
        """Take the room off a confirmed booking (it keeps its room type and nights)."""
        reservation = await self._load_for_change(reservation_id, version)
        code = reservation.confirmation_code
        if reservation.status is not ReservationStatus.CONFIRMED:
            raise InvalidTransitionError(
                f"Cannot unassign the room of {code}: it is {STATUS_PHRASES[reservation.status]}."
            )
        if reservation.room_id is None:
            return await self.view(reservation_id)
        room = await self.db.get_one(Room, reservation.room_id, populate_existing=True)
        touch(room)
        reservation.room_id = None
        self._audit(
            AuditAction.RESERVATION_ROOM_UNASSIGNED,
            reservation,
            {"room_id": room.id, "room_number": room.number},
            {"room_id": None, "room_number": None},
        )
        event = self._event(reservation, "room_unassigned")
        await self._commit_room_change(code, room.number)
        await self._publish(event, {room.id}, touches_today=False)
        return await self.view(reservation_id)

    # --- booking and check-in steps ----------------------------------------------------

    async def _book(
        self, data: ReservationCreate, idempotency_key: str, body_hash: str, endpoint: str
    ) -> Reservation | ReservationDetail:
        """Everything a booking does before commit: the new reservation (and guest, audit
        row and idempotency record) in the session, or the original if this is a retry."""
        replay = await self._replay(idempotency_key, body_hash, endpoint)
        if replay is not None:
            return replay

        prop = await self.db.get_one(Property, self.property_id)
        validate_stay(
            prop,
            self.today,
            check_in_date=data.check_in_date,
            check_out_date=data.check_out_date,
            adults=data.adults,
            children=data.children,
        )
        # Held until commit: other bookings of this type (and room setup changes that
        # take rooms from it) wait here.
        room_type = await lock_room_type(self.db, self.property_id, data.room_type_id)
        # A retry with the same key may have committed while we waited for the lock.
        replay = await self._replay(idempotency_key, body_hash, endpoint)
        if replay is not None:
            await self.db.rollback()
            return replay
        if not room_type.is_active:
            raise DomainError(
                f"Room type {room_type.code} is retired and cannot be booked.",
                code="room_type_retired",
            )
        if room_type.max_occupancy < data.adults + data.children:
            raise InvalidInputError(
                f"{room_type.name} sleeps at most {room_type.max_occupancy} "
                f"({data.adults + data.children} requested).",
                code="too_many_guests",
            )
        rate = self._nightly_rate(room_type, data.nightly_rate_minor)
        nights = stay_nights(data.check_in_date, data.check_out_date)
        free = (
            await AvailabilityService(self.db, self.today).free_rooms_by_night(
                self.property_id, nights, room_type_ids=[room_type.id]
            )
        ).get(room_type.id, {})
        full = [night for night in nights if free.get(night, 0) < 1]
        if full:
            raise NoAvailabilityError(
                f"No {room_type.name} room is free on {_dates(full)}.", unavailable_nights=full
            )

        guest = await self._guest(data)
        reservation = await self._insert(data, room_type, guest, rate, nights, prop.currency)
        record_change(
            self.db,
            action=AuditAction.RESERVATION_CREATED,
            target_type="reservation",
            target_id=reservation.id,
            actor=self.actor,
            property_id=self.property_id,
            client=self.client,
            before=None,
            after={
                "confirmation_code": reservation.confirmation_code,
                "status": ReservationStatus.CONFIRMED.value,
                "source": data.source.value,
                "guest_id": guest.id,
                "room_type_id": room_type.id,
                "room_type_code": room_type.code,
                "check_in_date": data.check_in_date.isoformat(),
                "check_out_date": data.check_out_date.isoformat(),
                "adults": data.adults,
                "children": data.children,
                "is_vip": data.is_vip,
                "has_notes": bool(data.notes),
                "nightly_rate_minor": rate,
                "rate_overridden": rate != room_type.base_rate_minor,
                "total_minor": reservation.total_minor,
                "currency": reservation.currency,
            },
        )
        replay = await self._store_key(idempotency_key, body_hash, reservation.id, endpoint)
        return replay if replay is not None else reservation

    async def _check_in_steps(self, reservation: Reservation, room: Room) -> None:
        """Check the rules and move the reservation into the room (no commit)."""
        if reservation.status is not ReservationStatus.CONFIRMED:
            raise InvalidTransitionError(
                f"Cannot check in: the reservation is {STATUS_PHRASES[reservation.status]}."
            )
        if reservation.check_in_date > self.today:
            raise DomainError(
                f"Reservation arrives on {reservation.check_in_date.isoformat()}; "
                "it cannot be checked in before then.",
                code="too_early",
            )
        if reservation.check_out_date <= self.today:
            raise DomainError("Reservation's stay has already ended.", code="stay_ended")
        self._ensure_room_type(room, reservation)
        if room.housekeeping_status not in READY_FOR_GUEST:
            raise DomainError(
                f"Room {room.number} is {HOUSEKEEPING_PHRASES[room.housekeeping_status]}; "
                "it must be cleaned first (awaiting inspection or ready).",
                code="room_not_ready",
            )
        await self._ensure_room_vacant(room, reservation)

        previous_room_id = reservation.room_id
        reservation.status = ReservationStatus.CHECKED_IN
        reservation.checked_in_at = func.now()
        reservation.room_id = room.id
        touch(room)
        self._audit(
            AuditAction.RESERVATION_CHECKED_IN,
            reservation,
            {"status": ReservationStatus.CONFIRMED.value, "room_id": previous_room_id},
            {
                "status": ReservationStatus.CHECKED_IN.value,
                "room_id": room.id,
                "room_number": room.number,
            },
        )

    @staticmethod
    def _ensure_room_type(room: Room, reservation: Reservation) -> None:
        if room.room_type_id != reservation.room_type_id:
            raise DomainError(
                f"Room {room.number} is not of the reserved room type.",
                code="room_type_mismatch",
            )

    async def _ensure_room_vacant(self, room: Room, reservation: Reservation) -> None:
        occupied = await self.db.scalar(
            select(Reservation.id).where(
                Reservation.room_id == room.id, Reservation.status == ReservationStatus.CHECKED_IN
            )
        )
        if occupied is not None:
            raise DomainError(
                f"Room {room.number} is occupied. Check the guest out first.",
                code="room_not_vacant",
            )
        held_by_other = await self.db.scalar(
            select(Reservation.id).where(
                Reservation.room_id == room.id,
                Reservation.status == ReservationStatus.CONFIRMED,
                Reservation.id != reservation.id,
                Reservation.check_in_date < reservation.check_out_date,
                Reservation.check_out_date > self.today,
            )
        )
        if held_by_other is not None:
            raise DomainError(
                f"Room {room.number} is assigned to another reservation for these nights.",
                code="room_not_vacant",
            )

    async def _load_for_update(self, reservation_id: int) -> Reservation:
        """Row lock without a version check (check-in and check-out use the room's)."""
        reservation = await self.db.scalar(
            select(Reservation)
            .where(Reservation.id == reservation_id, Reservation.property_id == self.property_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if reservation is None:
            raise NotFoundError(f"Reservation {reservation_id} not found")
        return reservation

    async def _commit_room_change(self, code: str, room_number: str) -> None:
        """Commit a change that moves a guest or a booking into or out of a room."""
        try:
            await self.db.commit()
        except StaleDataError:
            await self.db.rollback()
            raise VersionConflictError(
                f"Reservation {code} or room {room_number} was changed by someone else. "
                "Reload and try again."
            ) from None
        except IntegrityError as exc:
            # Database backstop for the vacancy checks (a concurrent check-in or
            # assignment won the race): the exclusion constraint or the one-party index.
            await self.db.rollback()
            text = str(exc.orig)
            if "ex_reservations_room_no_overlap" in text or "uq_reservations_room_in_house" in text:
                raise DomainError(
                    f"Room {room_number} was just taken by another reservation for these "
                    "nights. Choose another room.",
                    code="room_not_vacant",
                ) from None
            raise

    async def _detail(self, reservation_id: int) -> ReservationDetail:
        return await get_reservation_detail(
            self.db, reservation_id, include_contact=self._include_contact
        )

    # --- steps -----------------------------------------------------------------------

    async def _replay(
        self, key: str, body_hash: str, endpoint: str = CREATE_ENDPOINT
    ) -> ReservationDetail | None:
        record = await idempotency.find(self.db, key, self._actor_id)
        if record is None:
            return None
        reference = idempotency.replayed_reference(record, endpoint=endpoint, body_hash=body_hash)
        return await get_reservation_detail(
            self.db,
            int(reference.removeprefix("reservation:")),
            include_contact=self._include_contact,
        )

    def _nightly_rate(self, room_type: RoomType, override: int | None) -> int:
        """The room type's base rate, or the override. An override needs
        reservation.override_rate, unless it equals a base rate that is set."""
        base = room_type.base_rate_minor
        if override is not None and (override != base or base == 0):
            if P.RESERVATION_OVERRIDE_RATE not in self.permissions:
                raise PermissionDeniedError(
                    "Changing the nightly price needs the reservation.override_rate permission.",
                    code="rate_override_not_allowed",
                )
            return override
        if base == 0:
            raise DomainError(
                f"Room type {room_type.code} has no base rate yet. Enter a price, or ask an "
                "admin to set the rate.",
                code="rate_not_set",
            )
        return base

    async def _guest(self, data: ReservationCreate) -> Guest:
        guests = GuestService(
            self.db, self.actor, self.property_id, self.client, self._include_contact
        )
        if data.guest is not None:
            return await guests.add(data.guest)
        guest_id = data.guest_id or 0  # the schema requires guest or guest_id
        try:
            return await guests.load(guest_id)
        except NotFoundError:
            raise InvalidInputError(
                f"Guest {data.guest_id} does not exist.", code="guest_not_found"
            ) from None

    async def _insert(
        self,
        data: ReservationCreate,
        room_type: RoomType,
        guest: Guest,
        rate: int,
        nights: list[date],
        currency: str,
    ) -> Reservation:
        """Insert with a fresh random code, retrying (in a savepoint) on a collision."""
        for _ in range(CODE_ATTEMPTS):
            reservation = Reservation(
                property_id=self.property_id,
                confirmation_code=new_confirmation_code(),
                guest_id=guest.id,
                room_type_id=room_type.id,
                check_in_date=data.check_in_date,
                check_out_date=data.check_out_date,
                adults=data.adults,
                children=data.children,
                status=ReservationStatus.CONFIRMED,
                source=data.source,
                notes=data.notes or None,
                is_vip=data.is_vip,
                nights=[ReservationNight(night_date=night, rate_minor=rate) for night in nights],
                total_minor=rate * len(nights),
                currency=currency,
                created_by=self._actor_id,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(reservation)
            except IntegrityError as exc:
                if "uq_reservations_confirmation_code" not in str(exc.orig):
                    raise
                continue
            return reservation
        raise DomainError(
            "Could not allocate a confirmation code. Try again.", code="confirmation_code_failed"
        )

    async def _store_key(
        self, key: str, body_hash: str, reservation_id: int, endpoint: str = CREATE_ENDPOINT
    ) -> ReservationDetail | None:
        """Record the key. If a concurrent request with the same key won, undo this
        booking and answer like a retry (the original, or 422 for another body)."""
        try:
            async with self.db.begin_nested():
                self.db.add(
                    idempotency.new_record(
                        key=key,
                        user_id=self._actor_id,
                        endpoint=endpoint,
                        body_hash=body_hash,
                        reference=f"reservation:{reservation_id}",
                    )
                )
        except IntegrityError as exc:
            if "uq_idempotency_keys_key_user_id" not in str(exc.orig):
                raise
            await self.db.rollback()
            replay = await self._replay(key, body_hash, endpoint)
            if replay is None:  # pragma: no cover - the conflicting row exists
                raise
            return replay
        return None

    # --- changes ----------------------------------------------------------------------

    async def modify(
        self, reservation_id: int, data: ReservationUpdate, *, dry_run: bool = False
    ) -> ReservationView:
        """Change a booking in one transaction; on any refusal the original stays as it was.

        Confirmed: dates, room type, guests, notes, VIP, price. Checked in: only the
        departure date (not before today) and notes. Nights that remain keep their price,
        new nights get the current base rate, unless nightly_rate_minor re-prices them
        all (needs reservation.override_rate if any price changes).
        """
        reservation = await self._load_for_change(reservation_id, data.version)
        code = reservation.confirmation_code
        requested = {f: getattr(data, f) for f in data.model_fields_set - {"version"}}
        override = requested.pop("nightly_rate_minor", None)
        if "notes" in requested:
            requested["notes"] = requested["notes"] or None
        changes = {f: v for f, v in requested.items() if getattr(reservation, f) != v}

        status = reservation.status
        if status is ReservationStatus.CHECKED_IN:
            not_allowed = sorted(set(changes) - IN_HOUSE_CHANGES)
            if not_allowed:
                raise InvalidTransitionError(
                    f"The guest of {code} is checked in: only the departure date and notes "
                    f"can change (not {', '.join(not_allowed)})."
                )
        elif status is not ReservationStatus.CONFIRMED:
            raise InvalidTransitionError(f"Cannot change {code}: it is {STATUS_PHRASES[status]}.")
        if not changes and override is None:
            return await self.view(reservation_id)

        old = _Stay.of(reservation)
        new = _Stay(
            check_in_date=changes.get("check_in_date", old.check_in_date),
            check_out_date=changes.get("check_out_date", old.check_out_date),
            room_type_id=changes.get("room_type_id", old.room_type_id),
            adults=changes.get("adults", reservation.adults),
            children=changes.get("children", reservation.children),
        )
        stay_changed = new.nights_key != old.nights_key
        if stay_changed or {"adults", "children"} & set(changes):
            validate_stay(
                await self.db.get_one(Property, self.property_id),
                self.today,
                check_in_date=new.check_in_date,
                check_out_date=new.check_out_date,
                adults=new.adults,
                children=new.children,
                check_in_may_be_past=new.check_in_date == old.check_in_date,
            )

        # Consistent order (by id): two changes moving bookings between the same two
        # types cannot deadlock.
        types = {
            type_id: await lock_room_type(self.db, self.property_id, type_id)
            for type_id in sorted({old.room_type_id, new.room_type_id})
        }
        new_type = types[new.room_type_id]
        if new.room_type_id != old.room_type_id and not new_type.is_active:
            raise DomainError(
                f"Room type {new_type.code} is retired and cannot be booked.",
                code="room_type_retired",
            )
        if new_type.max_occupancy < new.adults + new.children:
            raise InvalidInputError(
                f"{new_type.name} sleeps at most {new_type.max_occupancy} "
                f"({new.adults + new.children} requested).",
                code="too_many_guests",
            )

        nights = await self._nights(reservation.id)
        new_nights = stay_nights(new.check_in_date, new.check_out_date)
        # Nights this booking already holds in the (unchanged) type need no new room.
        to_check = (
            new_nights
            if new.room_type_id != old.room_type_id
            else [night for night in new_nights if night not in nights]
        )
        if to_check:
            free = (
                await AvailabilityService(self.db, self.today).free_rooms_by_night(
                    self.property_id,
                    to_check,
                    room_type_ids=[new_type.id],
                    exclude_reservation_id=reservation.id,
                )
            ).get(new_type.id, {})
            full = [night for night in to_check if free.get(night, 0) < 1]
            if full:
                raise NoAvailabilityError(
                    f"No {new_type.name} room is free on {_dates(full)}; {code} is unchanged.",
                    unavailable_nights=full,
                )

        rates = self._reprice(nights, new_nights, new_type, override)

        # The assigned room stays only if it is still right for the new stay. Its
        # dashboard tile shows the stay's dates, notes and VIP flag.
        affected_rooms: set[int] = set()
        room_numbers = await self._room_numbers({reservation.room_id})
        before = self._snapshot(reservation, changes, nights, room_numbers)
        if reservation.room_id is not None and (stay_changed or TILE_FIELDS & set(changes)):
            room = await self.db.get_one(Room, reservation.room_id, populate_existing=True)
            affected_rooms.add(room.id)
            if stay_changed and not await self._room_still_fits(room, reservation.id, new):
                if status is ReservationStatus.CHECKED_IN:
                    raise DomainError(
                        f"Room {room.number} is booked for another guest on the new nights. "
                        "Move that booking to another room first.",
                        code="room_not_available",
                    )
                reservation.room_id = None
            touch(room)

        for field, value in changes.items():
            setattr(reservation, field, value)
        await self._write_nights(reservation.id, nights, rates)
        reservation.total_minor = sum(rates.values())
        reservation.updated_at = func.now()  # bumps the version even if only prices changed
        after = self._snapshot(reservation, changes, rates, room_numbers)
        if before.get("room_id") == after.get("room_id"):
            for field in ("room_id", "room_number"):
                before.pop(field, None)
                after.pop(field, None)
        self._audit(AuditAction.RESERVATION_UPDATED, reservation, before, after)

        if dry_run:
            # Preview: the booking as it would be, then everything is rolled back (no
            # audit row, no event). Version and history are the current ones.
            await self.db.flush()
            preview = await self.view(reservation_id)
            await self.db.rollback()
            return preview.model_copy(update={"version": data.version, "history": []})

        event = self._event(reservation, "updated")
        await self._commit(code)
        await self._publish(event, affected_rooms, touches_today=old.covers(self.today))
        return await self.view(reservation_id)

    async def cancel(self, reservation_id: int, data: ReservationCancel) -> ReservationView:
        """confirmed -> cancelled, with the reason. Frees the nights (status only)."""
        reservation = await self._load_for_change(reservation_id, data.version)
        if reservation.status is not ReservationStatus.CONFIRMED:
            raise InvalidTransitionError(
                f"Cannot cancel {reservation.confirmation_code}: it is "
                f"{STATUS_PHRASES[reservation.status]}."
            )
        return await self._end(reservation, ReservationStatus.CANCELLED, data.reason)

    async def mark_no_show(self, reservation_id: int, data: ReservationNoShow) -> ReservationView:
        """confirmed -> no_show, from the arrival day on."""
        reservation = await self._load_for_change(reservation_id, data.version)
        code = reservation.confirmation_code
        if reservation.status is not ReservationStatus.CONFIRMED:
            raise InvalidTransitionError(
                f"Cannot mark {code} as a no-show: it is {STATUS_PHRASES[reservation.status]}."
            )
        if self.today < reservation.check_in_date:
            raise DomainError(
                f"{code} arrives on {reservation.check_in_date.isoformat()}; it can be marked "
                "as a no-show from that day on.",
                code="too_early",
            )
        return await self._end(reservation, ReservationStatus.NO_SHOW, data.reason or None)

    async def view(self, reservation_id: int) -> ReservationView:
        return await get_reservation_view(
            self.db,
            reservation_id,
            include_contact=self._include_contact,
            property_id=self.property_id,
        )

    # --- change helpers ----------------------------------------------------------------

    async def _load_for_change(self, reservation_id: int, version: int) -> Reservation:
        # Row lock first (then room types by id, then rooms): concurrent changes to one
        # booking run one after the other.
        reservation = await self.db.scalar(
            select(Reservation)
            .where(Reservation.id == reservation_id, Reservation.property_id == self.property_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if reservation is None:
            raise NotFoundError(f"Reservation {reservation_id} not found")
        if reservation.version != version:
            raise VersionConflictError(
                f"Reservation {reservation.confirmation_code} was changed by someone else "
                f"(version {reservation.version}, you sent {version}). Reload and try again."
            )
        return reservation

    async def _end(
        self, reservation: Reservation, status: ReservationStatus, reason: str | None
    ) -> ReservationView:
        """Cancel or no-show: the status frees the nights; nothing is deleted. A room
        assigned in advance is released."""
        code = reservation.confirmation_code
        affected_rooms: set[int] = set()
        before: dict[str, object] = {"status": reservation.status.value}
        after: dict[str, object] = {"status": status.value, "reason": reason}
        if reservation.room_id is not None:
            room = await self.db.get_one(Room, reservation.room_id, populate_existing=True)
            touch(room)
            affected_rooms.add(room.id)
            before |= {"room_id": room.id, "room_number": room.number}
            after |= {"room_id": None, "room_number": None}
            reservation.room_id = None
        reservation.status = status
        reservation.cancelled_at = func.now()
        reservation.cancelled_by = self._actor_id
        reservation.cancellation_reason = reason
        self._audit(
            AuditAction.RESERVATION_CANCELLED
            if status is ReservationStatus.CANCELLED
            else AuditAction.RESERVATION_MARKED_NO_SHOW,
            reservation,
            before,
            after,
        )
        event = self._event(
            reservation, "cancelled" if status is ReservationStatus.CANCELLED else "no_show"
        )
        stay = _Stay.of(reservation)
        await self._commit(code)
        await self._publish(event, affected_rooms, touches_today=stay.covers(self.today))
        return await self.view(event.reservation_id)

    async def _nights(self, reservation_id: int) -> dict[date, int]:
        rows = await self.db.execute(
            select(ReservationNight.night_date, ReservationNight.rate_minor).where(
                ReservationNight.reservation_id == reservation_id
            )
        )
        return {key: value for key, value in rows}

    def _reprice(
        self,
        nights: dict[date, int],
        new_nights: list[date],
        room_type: RoomType,
        override: int | None,
    ) -> dict[date, int]:
        """Nights that remain keep their price, new nights get the base rate; an
        override prices them all. Any price that changes needs override_rate."""
        base = room_type.base_rate_minor
        if override is not None:
            rates = dict.fromkeys(new_nights, override)
        else:
            added = [night for night in new_nights if night not in nights]
            if added and base == 0:
                raise DomainError(
                    f"Room type {room_type.code} has no base rate yet. Enter a price, or ask "
                    "an admin to set the rate.",
                    code="rate_not_set",
                )
            rates = {night: nights.get(night, base) for night in new_nights}
        price_changed = any(rate != nights.get(night, base) for night, rate in rates.items())
        if price_changed and P.RESERVATION_OVERRIDE_RATE not in self.permissions:
            raise PermissionDeniedError(
                "Changing the nightly price needs the reservation.override_rate permission.",
                code="rate_override_not_allowed",
            )
        return rates

    async def _write_nights(
        self, reservation_id: int, old: dict[date, int], new: dict[date, int]
    ) -> None:
        gone = [night for night in old if night not in new]
        if gone:
            await self.db.execute(
                delete(ReservationNight).where(
                    ReservationNight.reservation_id == reservation_id,
                    ReservationNight.night_date.in_(gone),
                )
            )
        for night, rate in new.items():
            if night not in old:
                self.db.add(
                    ReservationNight(
                        reservation_id=reservation_id, night_date=night, rate_minor=rate
                    )
                )
            elif old[night] != rate:
                await self.db.execute(
                    update(ReservationNight)
                    .where(
                        ReservationNight.reservation_id == reservation_id,
                        ReservationNight.night_date == night,
                    )
                    .values(rate_minor=rate)
                )

    async def _room_still_fits(self, room: Room, reservation_id: int, stay: _Stay) -> bool:
        """Active, of the new type, and not held by another active stay on the new nights
        (nor by another guest still in house, if the stay includes tonight)."""
        if not room.is_active or room.room_type_id != stay.room_type_id:
            return False
        conflict = and_(
            Reservation.check_in_date < stay.check_out_date,
            Reservation.check_out_date > stay.check_in_date,
        )
        if stay.check_in_date <= self.today < stay.check_out_date:
            # Needs the room tonight: an overstaying guest still in it counts too (one due
            # out today does not: that is a turnover).
            conflict = or_(
                conflict,
                and_(
                    Reservation.status == ReservationStatus.CHECKED_IN,
                    Reservation.check_out_date < self.today,
                ),
            )
        other = await self.db.scalar(
            select(Reservation.id)
            .where(
                Reservation.room_id == room.id,
                Reservation.id != reservation_id,
                Reservation.status.in_(ACTIVE_STATUSES),
                conflict,
            )
            .limit(1)
        )
        return other is None

    async def _room_numbers(self, room_ids: set[int | None]) -> dict[int, str]:
        ids = [room_id for room_id in room_ids if room_id is not None]
        if not ids:
            return {}
        rows = await self.db.execute(select(Room.id, Room.number).where(Room.id.in_(ids)))
        return {key: value for key, value in rows}

    @staticmethod
    def _snapshot(
        reservation: Reservation,
        changes: dict[str, object],
        rates: dict[date, int],
        room_numbers: dict[int, str],
    ) -> dict[str, object]:
        snapshot: dict[str, object] = {
            field: _jsonable(getattr(reservation, field)) for field in changes
        }
        snapshot["nightly_rates"] = [
            {"night_date": night.isoformat(), "rate_minor": rate}
            for night, rate in sorted(rates.items())
        ]
        snapshot["total_minor"] = sum(rates.values())
        snapshot["room_id"] = reservation.room_id
        snapshot["room_number"] = room_numbers.get(reservation.room_id or 0)
        return snapshot

    def _audit(
        self,
        action: AuditAction,
        reservation: Reservation,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        record_change(
            self.db,
            action=action,
            target_type="reservation",
            target_id=reservation.id,
            actor=self.actor,
            property_id=self.property_id,
            client=self.client,
            before=before,
            after={**after, "confirmation_code": reservation.confirmation_code},
        )

    def _event(self, reservation: Reservation, change: ChangeKind) -> ReservationChanged:
        return ReservationChanged(
            property_id=self.property_id,
            reservation_id=reservation.id,
            change=change,
            status=reservation.status,
            room_type_id=reservation.room_type_id,
            check_in_date=reservation.check_in_date,
            check_out_date=reservation.check_out_date,
        )

    async def _commit(self, code: str) -> None:
        try:
            await self.db.commit()
        except StaleDataError:
            await self.db.rollback()
            raise VersionConflictError(
                f"Reservation {code} or its room was changed by someone else. Reload and try again."
            ) from None
        except IntegrityError as exc:
            await self.db.rollback()
            if "ex_reservations_room_no_overlap" in str(exc.orig):
                raise DomainError(
                    "The assigned room was just booked for another guest on those nights.",
                    code="room_not_available",
                ) from None
            raise

    async def _publish(
        self, event: ReservationChanged, rooms: set[int], *, touches_today: bool
    ) -> dict[int, DashboardRoom]:
        """reservation_changed, then the affected rooms' tiles and the summary (or just
        the summary when the stay touches today). Returns the room entries."""
        await self.bus.publish(event)
        stay = _Stay(
            check_in_date=event.check_in_date,
            check_out_date=event.check_out_date,
            room_type_id=event.room_type_id,
        )
        if rooms:
            # Room tiles changed; this also sends the summary.
            return await publish_room_changes(self.db, self.bus, self.today, rooms)
        if touches_today or stay.covers(self.today):
            await self.bus.publish(
                SummaryChanged(summary=await get_dashboard_summary(self.db, self.today))
            )
        return {}


IN_HOUSE_CHANGES: frozenset[str] = frozenset({"check_out_date", "notes"})
type ChangeKind = Literal[
    "created",
    "updated",
    "cancelled",
    "no_show",
    "room_assigned",
    "room_unassigned",
    "checked_in",
    "checked_out",
]
# Reservation fields shown on the assigned room's dashboard tile (besides the dates).
TILE_FIELDS: frozenset[str] = frozenset({"notes", "is_vip"})


@dataclass(frozen=True)
class _Stay:
    check_in_date: date
    check_out_date: date
    room_type_id: int
    adults: int = 1
    children: int = 0

    @classmethod
    def of(cls, reservation: Reservation) -> _Stay:
        return cls(
            check_in_date=reservation.check_in_date,
            check_out_date=reservation.check_out_date,
            room_type_id=reservation.room_type_id,
            adults=reservation.adults,
            children=reservation.children,
        )

    @property
    def nights_key(self) -> tuple[date, date, int]:
        return (self.check_in_date, self.check_out_date, self.room_type_id)

    def covers(self, day: date) -> bool:
        """Arrives, stays or leaves that day (the dashboard counts all three)."""
        return self.check_in_date <= day <= self.check_out_date


def _jsonable(value: object) -> object:
    return value.isoformat() if isinstance(value, date) else value


async def get_reservation_view(
    db: AsyncSession, reservation_id: int, *, include_contact: bool, property_id: int | None = None
) -> ReservationView:
    """The detail plus its history from the audit log, oldest first."""
    detail = await get_reservation_detail(
        db, reservation_id, include_contact=include_contact, property_id=property_id
    )
    rows = (
        await db.execute(
            select(AuditLog, User.id, User.full_name)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(AuditLog.target_type == "reservation", AuditLog.target_id == str(reservation_id))
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    ).all()
    history = []
    for entry, actor_id, actor_name in rows:
        before, after = entry.before or {}, entry.after or {}
        history.append(
            ReservationHistoryEntry(
                action=entry.action,
                occurred_at=entry.created_at,
                actor=HistoryActor(id=actor_id, full_name=actor_name) if actor_id else None,
                status_before=before.get("status"),
                status_after=after.get("status"),
                changed=sorted((set(before) | set(after)) - HISTORY_CONTEXT_FIELDS),
                reason=after.get("reason"),
            )
        )
    return ReservationView(**detail.model_dump(), history=history)


# Recorded for context, not as changes.
HISTORY_CONTEXT_FIELDS: frozenset[str] = frozenset({"confirmation_code", "room_number"})


async def find_reservation_id_by_code(db: AsyncSession, code: str, property_id: int) -> int:
    normalised = code.strip().upper()
    reservation_id = await db.scalar(
        select(Reservation.id).where(
            Reservation.confirmation_code == normalised, Reservation.property_id == property_id
        )
    )
    if reservation_id is None:
        raise NotFoundError(f"No reservation with the code {normalised}")
    return reservation_id
