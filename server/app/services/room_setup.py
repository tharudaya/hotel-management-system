"""Room setup: room types and rooms of one property (see "Room setup" in CLAUDE.md).

Rules:

- Rooms and room types are never deleted, only retired (and can be reactivated).
- A room cannot be retired while a guest is checked in to it or an upcoming
  reservation is assigned to it.
- A room type cannot be retired while it has active rooms or upcoming reservations.
- A room's type cannot be changed while a guest is checked in to that room.
- Rooms cannot be put in (or reactivated with) a retired room type.
- Room numbers and room type codes are trimmed and stored upper-case, so they are
  unique per property regardless of case; room type names are unique ignoring case.
- Room edits use optimistic locking (the client sends the version it saw; 409 if
  someone else changed the room meanwhile).
- Every change writes one audit_log row with before/after, then publishes
  room_setup_changed so dashboards refetch rooms and the summary.

"Upcoming" reservations are confirmed ones that have not ended yet (check-out after
today) plus guests in house.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.orm.exc import StaleDataError

from app.models import (
    AuditAction,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
    User,
)
from app.realtime.bus import EventBus
from app.realtime.events import RoomSetupChanged
from app.schemas.room_setup import (
    RoomCreate,
    RoomListQuery,
    RoomOut,
    RoomTypeCreate,
    RoomTypeListQuery,
    RoomTypeOut,
    RoomTypeRef,
    RoomTypeUpdate,
    RoomUpdate,
    UserRef,
)
from app.services.audit import ClientInfo, record_change
from app.services.errors import (
    DomainError,
    InvalidTransitionError,
    NotFoundError,
    VersionConflictError,
)

ROOM_FIELDS: tuple[str, ...] = ("number", "floor", "room_type_id", "notes")
ROOM_TYPE_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "max_occupancy",
    "bed_setup",
    "description",
)

# Sort "99" before "101" and keep names like "PH1" after the plain numbers.
ROOM_NUMBER_ORDER: tuple[ColumnElement[Any] | InstrumentedAttribute[str], ...] = (
    case((Room.number.regexp_match("^[0-9]+$"), 0), else_=1),
    func.length(Room.number),
    Room.number,
)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _jsonable(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class _RoomUse:
    guest_in_house: bool
    upcoming: int


@dataclass
class RoomSetupService:
    """Acts on behalf of `actor` at one property. Construct per request."""

    db: AsyncSession
    bus: EventBus
    actor: User
    property_id: int
    client: ClientInfo
    today: date

    # --- room types: reads ---------------------------------------------------------

    async def list_room_types(self, query: RoomTypeListQuery) -> list[RoomTypeOut]:
        stmt = select(RoomType).where(RoomType.property_id == self.property_id)
        if not query.include_retired:
            stmt = stmt.where(RoomType.is_active)
        types = list((await self.db.scalars(stmt.order_by(RoomType.name))).all())
        return await self._room_types_out(types)

    # --- room types: writes ------------------------------------------------------

    async def create_room_type(self, data: RoomTypeCreate) -> RoomTypeOut:
        code = data.code.upper()
        await self._ensure_type_unique(code=code, name=data.name)
        room_type = RoomType(
            property_id=self.property_id,
            code=code,
            name=data.name,
            max_occupancy=data.max_occupancy,
            bed_setup=data.bed_setup or None,
            description=data.description or None,
        )
        self.db.add(room_type)
        await self._flush()
        self._audit(
            AuditAction.ROOM_TYPE_CREATED,
            "room_type",
            room_type.id,
            before=None,
            after={f: getattr(room_type, f) for f in ROOM_TYPE_FIELDS},
        )
        await self._commit()
        await self._publish()
        return (await self._room_types_out([room_type]))[0]

    async def update_room_type(self, room_type_id: int, data: RoomTypeUpdate) -> RoomTypeOut:
        room_type = await self._load_type(room_type_id, lock=True)
        changes: dict[str, object] = {}
        for field in data.model_fields_set:
            value = getattr(data, field)
            if field == "code":
                value = value.upper()
            elif field in ("bed_setup", "description"):
                value = value or None
            if getattr(room_type, field) != value:
                changes[field] = value
        if not changes:
            return (await self._room_types_out([room_type]))[0]
        await self._ensure_type_unique(
            code=str(changes["code"]) if "code" in changes else None,
            name=str(changes["name"]) if "name" in changes else None,
            exclude_id=room_type.id,
        )
        before = {f: getattr(room_type, f) for f in changes}
        for field, value in changes.items():
            setattr(room_type, field, value)
        await self._flush()
        self._audit(
            AuditAction.ROOM_TYPE_UPDATED, "room_type", room_type.id, before=before, after=changes
        )
        await self._commit()
        await self._publish()
        return (await self._room_types_out([room_type]))[0]

    async def retire_room_type(self, room_type_id: int) -> RoomTypeOut:
        room_type = await self._load_type(room_type_id, lock=True)
        if not room_type.is_active:
            raise InvalidTransitionError(f"Room type {room_type.code} is already retired.")
        reason = (await self._type_retire_blocked([room_type])).get(room_type.id)
        if reason:
            raise DomainError(reason, code="room_type_in_use")
        room_type.is_active = False
        self._audit(
            AuditAction.ROOM_TYPE_RETIRED,
            "room_type",
            room_type.id,
            before={"is_active": True},
            after={"is_active": False, "code": room_type.code},
        )
        await self._commit()
        await self._publish()
        return (await self._room_types_out([room_type]))[0]

    async def reactivate_room_type(self, room_type_id: int) -> RoomTypeOut:
        room_type = await self._load_type(room_type_id, lock=True)
        if room_type.is_active:
            raise InvalidTransitionError(f"Room type {room_type.code} is not retired.")
        room_type.is_active = True
        self._audit(
            AuditAction.ROOM_TYPE_REACTIVATED,
            "room_type",
            room_type.id,
            before={"is_active": False},
            after={"is_active": True, "code": room_type.code},
        )
        await self._commit()
        await self._publish()
        return (await self._room_types_out([room_type]))[0]

    # --- rooms: reads ------------------------------------------------------------

    async def list_rooms(self, query: RoomListQuery) -> list[RoomOut]:
        stmt = select(Room).where(Room.property_id == self.property_id)
        if query.floor is not None:
            stmt = stmt.where(Room.floor == query.floor)
        if query.room_type_id is not None:
            stmt = stmt.where(Room.room_type_id == query.room_type_id)
        if query.status == "active":
            stmt = stmt.where(Room.is_active)
        elif query.status == "retired":
            stmt = stmt.where(~Room.is_active)
        rooms = list((await self.db.scalars(stmt.order_by(*ROOM_NUMBER_ORDER))).all())
        return await self._rooms_out(rooms)

    # --- rooms: writes -----------------------------------------------------------

    async def create_room(self, data: RoomCreate) -> RoomOut:
        number = data.number.upper()
        room_type = await self._active_type(data.room_type_id)
        await self._ensure_number_free(number)
        room = Room(
            property_id=self.property_id,
            number=number,
            floor=data.floor,
            room_type_id=room_type.id,
            notes=data.notes or None,
            # A new room is ready to sell.
            housekeeping_status=HousekeepingStatus.INSPECTED,
        )
        self.db.add(room)
        await self._flush()
        self._audit(
            AuditAction.ROOM_CREATED,
            "room",
            room.id,
            before=None,
            after={
                **{f: getattr(room, f) for f in ROOM_FIELDS},
                "room_type_code": room_type.code,
                "housekeeping_status": room.housekeeping_status.value,
            },
        )
        await self._commit()
        await self._publish()
        return (await self._rooms_out([room]))[0]

    async def update_room(self, room_id: int, data: RoomUpdate) -> RoomOut:
        room = await self._load_room(room_id)
        self._ensure_version(room, data.version)
        changes: dict[str, object] = {}
        for field in data.model_fields_set - {"version"}:
            value = getattr(data, field)
            if field == "number":
                value = value.upper()
            elif field == "notes":
                value = value or None
            if getattr(room, field) != value:
                changes[field] = value
        if not changes:
            return (await self._rooms_out([room]))[0]

        before: dict[str, object] = {f: getattr(room, f) for f in changes}
        after: dict[str, object] = dict(changes)
        if "room_type_id" in changes:
            if (await self._room_use([room.id]))[room.id].guest_in_house:
                raise DomainError(
                    f"Room {room.number} has a guest checked in, so its room type cannot "
                    "change. Try again after check-out.",
                    code="room_in_use",
                )
            new_type = await self._active_type(int(str(changes["room_type_id"])))
            old_type = await self.db.get(RoomType, room.room_type_id)
            before["room_type_code"] = old_type.code if old_type else None
            after["room_type_code"] = new_type.code
        if "number" in changes:
            await self._ensure_number_free(str(changes["number"]), exclude_id=room.id)

        for field, value in changes.items():
            setattr(room, field, value)
        self._audit(AuditAction.ROOM_UPDATED, "room", room.id, before=before, after=after)
        await self._commit(room)
        await self._publish()
        return (await self._rooms_out([room]))[0]

    async def retire_room(self, room_id: int, version: int) -> RoomOut:
        room = await self._load_room(room_id)
        self._ensure_version(room, version)
        if not room.is_active:
            raise InvalidTransitionError(f"Room {room.number} is already retired.")
        reason = self._room_retire_blocked(room, (await self._room_use([room.id]))[room.id])
        if reason:
            raise DomainError(reason, code="room_in_use")
        now = datetime.now(UTC)
        room.is_active = False
        room.retired_at = now
        room.retired_by = self.actor.id
        self._audit(
            AuditAction.ROOM_RETIRED,
            "room",
            room.id,
            before={"is_active": True},
            after={"is_active": False, "retired_at": now.isoformat(), "number": room.number},
        )
        await self._commit(room)
        await self._publish()
        return (await self._rooms_out([room]))[0]

    async def reactivate_room(self, room_id: int, version: int) -> RoomOut:
        room = await self._load_room(room_id)
        self._ensure_version(room, version)
        if room.is_active:
            raise InvalidTransitionError(f"Room {room.number} is not retired.")
        room_type = await self.db.get(RoomType, room.room_type_id)
        if room_type is None or not room_type.is_active:
            code = room_type.code if room_type else "?"
            raise DomainError(
                f"Room {room.number} uses the retired room type {code}. Reactivate the room "
                "type first, or give the room another type.",
                code="room_type_retired",
            )
        before = {"is_active": False, "retired_at": _jsonable(room.retired_at)}
        room.is_active = True
        room.retired_at = None
        room.retired_by = None
        self._audit(
            AuditAction.ROOM_REACTIVATED,
            "room",
            room.id,
            before=before,
            after={"is_active": True, "number": room.number},
        )
        await self._commit(room)
        await self._publish()
        return (await self._rooms_out([room]))[0]

    # --- rules -------------------------------------------------------------------

    def _upcoming(self) -> ColumnElement[bool]:
        """Reservations that still need their room (type): not over, or in house."""
        return or_(
            Reservation.status == ReservationStatus.CHECKED_IN,
            and_(
                Reservation.status == ReservationStatus.CONFIRMED,
                Reservation.check_out_date > self.today,
            ),
        )

    async def _room_use(self, room_ids: list[int]) -> dict[int, _RoomUse]:
        """Guest in house and upcoming assigned reservations, per room, in one query."""
        rows = (
            await self.db.execute(
                select(
                    Reservation.room_id,
                    func.count().filter(Reservation.status == ReservationStatus.CHECKED_IN),
                    func.count().filter(Reservation.status == ReservationStatus.CONFIRMED),
                )
                .where(Reservation.room_id.in_(room_ids), self._upcoming())
                .group_by(Reservation.room_id)
            )
        ).all()
        found = {room_id: _RoomUse(in_house > 0, upcoming) for room_id, in_house, upcoming in rows}
        return {room_id: found.get(room_id, _RoomUse(False, 0)) for room_id in room_ids}

    @staticmethod
    def _room_retire_blocked(room: Room, use: _RoomUse) -> str | None:
        if use.guest_in_house:
            return f"Room {room.number} has a guest checked in."
        if use.upcoming:
            return (
                f"Room {room.number} has {_plural(use.upcoming, 'upcoming reservation')} "
                "assigned. Move them to another room first."
            )
        return None

    async def _type_counts(self, type_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
        """(active rooms, upcoming reservations) per room type."""
        active = dict(
            (
                await self.db.execute(
                    select(Room.room_type_id, func.count())
                    .where(Room.room_type_id.in_(type_ids), Room.is_active)
                    .group_by(Room.room_type_id)
                )
            ).all()
        )
        upcoming = dict(
            (
                await self.db.execute(
                    select(Reservation.room_type_id, func.count())
                    .where(Reservation.room_type_id.in_(type_ids), self._upcoming())
                    .group_by(Reservation.room_type_id)
                )
            ).all()
        )
        return active, upcoming

    async def _type_retire_blocked(self, types: list[RoomType]) -> dict[int, str | None]:
        active, upcoming = await self._type_counts([t.id for t in types])
        result: dict[int, str | None] = {}
        for t in types:
            rooms, booked = active.get(t.id, 0), upcoming.get(t.id, 0)
            if rooms:
                result[t.id] = (
                    f"Room type {t.code} still has {_plural(rooms, 'active room')}. Retire "
                    "them or give them another type first."
                )
            elif booked:
                result[t.id] = f"Room type {t.code} has {_plural(booked, 'upcoming reservation')}."
            else:
                result[t.id] = None
        return result

    async def _active_type(self, room_type_id: int) -> RoomType:
        room_type = await self._load_type(room_type_id)
        if not room_type.is_active:
            raise DomainError(
                f"Room type {room_type.code} is retired. Choose an active room type.",
                code="room_type_retired",
            )
        return room_type

    async def _ensure_type_unique(
        self, *, code: str | None = None, name: str | None = None, exclude_id: int = 0
    ) -> None:
        scope = and_(RoomType.property_id == self.property_id, RoomType.id != exclude_id)
        if code is not None:
            taken = await self.db.scalar(
                select(RoomType).where(scope, func.upper(RoomType.code) == code.upper())
            )
            if taken is not None:
                retired = "" if taken.is_active else " (retired; reactivate it instead)"
                raise DomainError(
                    f"A room type with the code {taken.code} already exists{retired}.",
                    code="room_type_code_taken",
                )
        if name is not None:
            taken = await self.db.scalar(
                select(RoomType).where(scope, func.lower(RoomType.name) == name.lower())
            )
            if taken is not None:
                raise DomainError(
                    f"A room type named {taken.name!r} already exists.",
                    code="room_type_name_taken",
                )

    async def _ensure_number_free(self, number: str, *, exclude_id: int = 0) -> None:
        taken = await self.db.scalar(
            select(Room).where(
                Room.property_id == self.property_id,
                func.upper(Room.number) == number.upper(),
                Room.id != exclude_id,
            )
        )
        if taken is not None:
            retired = "" if taken.is_active else " (retired; reactivate it instead)"
            raise DomainError(
                f"Room {taken.number} already exists{retired}.", code="room_number_taken"
            )

    @staticmethod
    def _ensure_version(room: Room, expected: int) -> None:
        if room.version != expected:
            raise VersionConflictError(
                f"Room {room.number} was changed by someone else. Reload and try again."
            )

    # --- loading -----------------------------------------------------------------

    async def _load_type(self, room_type_id: int, *, lock: bool = False) -> RoomType:
        stmt = (
            select(RoomType)
            .where(RoomType.id == room_type_id, RoomType.property_id == self.property_id)
            .execution_options(populate_existing=True)
        )
        # Retiring re-checks the type's rooms; lock so a room cannot be added meanwhile.
        room_type = await self.db.scalar(stmt.with_for_update() if lock else stmt)
        if room_type is None:
            raise NotFoundError(f"Room type {room_type_id} not found")
        return room_type

    async def _load_room(self, room_id: int) -> Room:
        room = await self.db.scalar(
            select(Room)
            .where(Room.id == room_id, Room.property_id == self.property_id)
            .execution_options(populate_existing=True)
        )
        if room is None:
            raise NotFoundError(f"Room {room_id} not found")
        return room

    # --- output ------------------------------------------------------------------

    async def _room_types_out(self, types: list[RoomType]) -> list[RoomTypeOut]:
        for t in types:
            await self.db.refresh(t)
        active, upcoming = await self._type_counts([t.id for t in types])
        blocked = await self._type_retire_blocked(types)
        return [
            RoomTypeOut(
                id=t.id,
                code=t.code,
                name=t.name,
                max_occupancy=t.max_occupancy,
                bed_setup=t.bed_setup,
                description=t.description,
                is_active=t.is_active,
                active_room_count=active.get(t.id, 0),
                upcoming_reservations=upcoming.get(t.id, 0),
                retire_blocked=blocked[t.id] if t.is_active else None,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in types
        ]

    async def _rooms_out(self, rooms: list[Room]) -> list[RoomOut]:
        if not rooms:
            return []
        for room in rooms:
            await self.db.refresh(room)
        type_ids = {room.room_type_id for room in rooms}
        types = {
            t.id: t
            for t in (await self.db.scalars(select(RoomType).where(RoomType.id.in_(type_ids))))
        }
        retirer_ids = {room.retired_by for room in rooms if room.retired_by is not None}
        retirers = (
            {u.id: u for u in (await self.db.scalars(select(User).where(User.id.in_(retirer_ids))))}
            if retirer_ids
            else {}
        )
        use = await self._room_use([room.id for room in rooms])
        out: list[RoomOut] = []
        for room in rooms:
            room_type = types[room.room_type_id]
            retirer = retirers.get(room.retired_by) if room.retired_by is not None else None
            room_use = use[room.id]
            out.append(
                RoomOut(
                    id=room.id,
                    number=room.number,
                    floor=room.floor,
                    room_type=RoomTypeRef(
                        id=room_type.id,
                        code=room_type.code,
                        name=room_type.name,
                        is_active=room_type.is_active,
                    ),
                    notes=room.notes,
                    housekeeping_status=room.housekeeping_status,
                    is_active=room.is_active,
                    retired_at=room.retired_at,
                    retired_by=(
                        UserRef(
                            id=retirer.id, username=retirer.username, full_name=retirer.full_name
                        )
                        if retirer
                        else None
                    ),
                    guest_in_house=room_use.guest_in_house,
                    upcoming_reservations=room_use.upcoming,
                    retire_blocked=(
                        self._room_retire_blocked(room, room_use) if room.is_active else None
                    ),
                    type_change_blocked=(
                        f"Room {room.number} has a guest checked in."
                        if room_use.guest_in_house
                        else None
                    ),
                    version=room.version,
                    created_at=room.created_at,
                    updated_at=room.updated_at,
                )
            )
        return out

    # --- persistence and side effects ------------------------------------------

    async def _flush(self) -> None:
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise self._conflict(exc) from None

    async def _commit(self, room: Room | None = None) -> None:
        number = room.number if room is not None else None  # rollback expires objects
        try:
            await self.db.commit()
        except StaleDataError:
            await self.db.rollback()
            raise VersionConflictError(
                f"Room {number} was changed by someone else. Reload and try again."
            ) from None
        except IntegrityError as exc:
            await self.db.rollback()
            raise self._conflict(exc) from None

    @staticmethod
    def _conflict(exc: IntegrityError) -> DomainError:
        """A unique-constraint race lost after the pre-check: same answer as the check."""
        text = str(exc.orig)
        if "uq_rooms_property_id_number" in text:
            return DomainError("That room number already exists.", code="room_number_taken")
        if "uq_room_types_property_id_code" in text:
            return DomainError("That room type code already exists.", code="room_type_code_taken")
        if "uq_room_types_property_id_name" in text:
            return DomainError("That room type name already exists.", code="room_type_name_taken")
        raise exc

    def _audit(
        self,
        action: AuditAction,
        target_type: str,
        target_id: int,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        record_change(
            self.db,
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor=self.actor,
            property_id=self.property_id,
            client=self.client,
            before={k: _jsonable(v) for k, v in before.items()} if before else before,
            after={k: _jsonable(v) for k, v in after.items()} if after else after,
        )

    async def _publish(self) -> None:
        # Room setup can change any tile and every count: dashboards refetch both.
        await self.bus.publish(RoomSetupChanged(property_id=self.property_id))
