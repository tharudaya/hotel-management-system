"""Reservations: details, creation (ReservationService), check-in and check-out.

See "Reservations" in CLAUDE.md for the rules. Creating a booking runs in one
transaction: idempotency check, stay validation, room type row lock, availability
recount under the lock, guest, reservation with its nights, audit row, idempotency
record; events are published after commit.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.confirmation_codes import new_confirmation_code
from app.core.permissions import P
from app.models import (
    AuditAction,
    Guest,
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationNight,
    ReservationStatus,
    Room,
    RoomType,
    User,
)
from app.realtime.bus import EventBus
from app.realtime.events import ReservationChanged, SummaryChanged
from app.schemas.availability import NightlyRate
from app.schemas.dashboard import DashboardRoom
from app.schemas.reservations import GuestOut, ReservationCreate, ReservationDetail
from app.services import idempotency
from app.services.audit import ClientInfo, record_change
from app.services.availability import AvailabilityService, lock_room_type
from app.services.dashboard import get_dashboard_summary
from app.services.errors import (
    DomainError,
    InvalidInputError,
    InvalidTransitionError,
    NoAvailabilityError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.guests import GuestService
from app.services.room_changes import (
    commit,
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
# Codes are random (31**8 of them): a collision is rare, several in a row mean a bug.
CODE_ATTEMPTS: int = 5


async def get_reservation_detail(
    db: AsyncSession, reservation_id: int, *, include_contact: bool
) -> ReservationDetail:
    row = (
        await db.execute(
            select(Reservation, Guest, RoomType, Room)
            .join(Reservation.guest)
            .join(Reservation.room_type)
            .outerjoin(Reservation.room)
            .where(Reservation.id == reservation_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
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
        replay = await self._replay(idempotency_key, body_hash)
        if replay is not None:
            return replay, True

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
        replay = await self._replay(idempotency_key, body_hash)
        if replay is not None:
            await self.db.rollback()
            return replay, True
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
        replay = await self._store_key(idempotency_key, body_hash, reservation.id)
        if replay is not None:
            return replay, True
        event = ReservationChanged(
            property_id=self.property_id,
            reservation_id=reservation.id,
            change="created",
            status=ReservationStatus.CONFIRMED,
            room_type_id=room_type.id,
            check_in_date=data.check_in_date,
            check_out_date=data.check_out_date,
        )
        await self.db.commit()

        await self.bus.publish(event)
        if data.check_in_date <= self.today < data.check_out_date:
            # Arrivals today and rooms sold tonight changed.
            await self.bus.publish(
                SummaryChanged(summary=await get_dashboard_summary(self.db, self.today))
            )
        detail = await get_reservation_detail(
            self.db, event.reservation_id, include_contact=self._include_contact
        )
        return detail, False

    # --- steps -----------------------------------------------------------------------

    async def _replay(self, key: str, body_hash: str) -> ReservationDetail | None:
        record = await idempotency.find(self.db, key, self._actor_id)
        if record is None:
            return None
        reference = idempotency.replayed_reference(
            record, endpoint=CREATE_ENDPOINT, body_hash=body_hash
        )
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
        self, key: str, body_hash: str, reservation_id: int
    ) -> ReservationDetail | None:
        """Record the key. If a concurrent request with the same key won, undo this
        booking and answer like a retry (the original, or 422 for another body)."""
        try:
            async with self.db.begin_nested():
                self.db.add(
                    idempotency.new_record(
                        key=key,
                        user_id=self._actor_id,
                        endpoint=CREATE_ENDPOINT,
                        body_hash=body_hash,
                        reference=f"reservation:{reservation_id}",
                    )
                )
        except IntegrityError as exc:
            if "uq_idempotency_keys_key_user_id" not in str(exc.orig):
                raise
            await self.db.rollback()
            replay = await self._replay(key, body_hash)
            if replay is None:  # pragma: no cover - the conflicting row exists
                raise
            return replay
        return None


async def _load_reservation_for_update(db: AsyncSession, reservation_id: int) -> Reservation:
    # Row lock: concurrent check-ins/outs of the same reservation run one after
    # the other, and the second sees the first one's result.
    reservation = await db.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reservation is None:
        raise NotFoundError(f"Reservation {reservation_id} not found")
    return reservation


async def _ensure_room_free_for(
    db: AsyncSession, room: Room, reservation: Reservation, today: date
) -> None:
    occupied = await db.scalar(
        select(Reservation.id).where(
            Reservation.room_id == room.id, Reservation.status == ReservationStatus.CHECKED_IN
        )
    )
    if occupied is not None:
        raise DomainError(f"Room {room.number} is occupied.", code="room_not_vacant")

    held_by_other = await db.scalar(
        select(Reservation.id).where(
            Reservation.room_id == room.id,
            Reservation.status == ReservationStatus.CONFIRMED,
            Reservation.id != reservation.id,
            Reservation.check_in_date < reservation.check_out_date,
            Reservation.check_out_date > today,
        )
    )
    if held_by_other is not None:
        raise DomainError(
            f"Room {room.number} is assigned to another reservation for these nights.",
            code="room_not_vacant",
        )


async def check_in(
    db: AsyncSession,
    bus: EventBus,
    *,
    reservation_id: int,
    room_id: int,
    expected_room_version: int,
    actor: User,
    property_id: int,
    client: ClientInfo,
    today: date,
) -> DashboardRoom:
    reservation = await _load_reservation_for_update(db, reservation_id)
    if reservation.status is not ReservationStatus.CONFIRMED:
        raise InvalidTransitionError(
            f"Cannot check in: the reservation is {STATUS_PHRASES[reservation.status]}."
        )
    if reservation.check_in_date > today:
        raise DomainError(
            f"Reservation arrives on {reservation.check_in_date.isoformat()}; "
            "it cannot be checked in before then.",
            code="too_early",
        )
    if reservation.check_out_date <= today:
        raise DomainError("Reservation's stay has already ended.", code="stay_ended")

    room = await load_room(db, room_id)
    ensure_version(room, expected_room_version)
    if room.room_type_id != reservation.room_type_id:
        raise DomainError(
            f"Room {room.number} is not of the reserved room type.", code="room_type_mismatch"
        )
    if room.housekeeping_status not in READY_FOR_GUEST:
        raise DomainError(
            f"Room {room.number} is {HOUSEKEEPING_PHRASES[room.housekeeping_status]}; "
            "it must be cleaned first (awaiting inspection or ready).",
            code="room_not_ready",
        )
    await _ensure_room_free_for(db, room, reservation, today)

    previous_room_id = reservation.room_id
    room_number = room.number  # read now: a failed commit expires loaded objects
    reservation.status = ReservationStatus.CHECKED_IN
    reservation.checked_in_at = func.now()
    reservation.room_id = room.id
    touch(room)
    record_change(
        db,
        action=AuditAction.RESERVATION_CHECKED_IN,
        target_type="reservation",
        target_id=reservation.id,
        actor=actor,
        property_id=property_id,
        client=client,
        before={"status": ReservationStatus.CONFIRMED.value, "room_id": previous_room_id},
        after={
            "status": ReservationStatus.CHECKED_IN.value,
            "room_id": room.id,
            "room_number": room.number,
            "confirmation_code": reservation.confirmation_code,
        },
    )
    try:
        await commit(db, room)
    except IntegrityError:
        # Database backstop for the vacancy checks above (a concurrent check-in
        # or assignment won the race).
        await db.rollback()
        raise DomainError(
            f"Room {room_number} was just taken by another reservation.", code="room_not_vacant"
        ) from None

    # Moving the guest from a pre-assigned room changes that room's entry too.
    affected = {room_id}
    if previous_room_id is not None:
        affected.add(previous_room_id)
    entries = await publish_room_changes(db, bus, today, affected)
    return entries[room_id]


async def check_out(
    db: AsyncSession,
    bus: EventBus,
    *,
    reservation_id: int,
    expected_room_version: int,
    actor: User,
    property_id: int,
    client: ClientInfo,
    today: date,
) -> DashboardRoom:
    reservation = await _load_reservation_for_update(db, reservation_id)
    if reservation.status is not ReservationStatus.CHECKED_IN or reservation.room_id is None:
        raise InvalidTransitionError(
            f"Cannot check out: the reservation is {STATUS_PHRASES[reservation.status]}."
        )

    room = await load_room(db, reservation.room_id)
    ensure_version(room, expected_room_version)
    housekeeping_before = room.housekeeping_status

    reservation.status = ReservationStatus.CHECKED_OUT
    reservation.checked_out_at = func.now()
    room.housekeeping_status = HousekeepingStatus.DIRTY
    touch(room)  # bumps the version even if the room was already dirty
    record_change(
        db,
        action=AuditAction.RESERVATION_CHECKED_OUT,
        target_type="reservation",
        target_id=reservation.id,
        actor=actor,
        property_id=property_id,
        client=client,
        before={
            "status": ReservationStatus.CHECKED_IN.value,
            "housekeeping_status": housekeeping_before.value,
        },
        after={
            "status": ReservationStatus.CHECKED_OUT.value,
            "housekeeping_status": HousekeepingStatus.DIRTY.value,
            "room_id": room.id,
            "room_number": room.number,
            "confirmation_code": reservation.confirmation_code,
        },
    )
    await commit(db, room)

    entries = await publish_room_changes(db, bus, today, [room.id])
    return entries[room.id]
