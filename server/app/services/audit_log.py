"""Reading the audit trail (writing is services/audit.record_change / record_event).

Data changes (audit_log) and authentication events (audit_events) are read as one
stream, newest first. Each source is a SELECT with the same columns, filtered on its
own, then combined with UNION ALL. Pagination is keyset ("cursor") on
(occurred_at, source, row_id): stable while new rows arrive and cheap at any depth.
"""

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    case,
    cast,
    func,
    literal,
    null,
    or_,
    select,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models import AuditAction, AuditEvent, AuditEventType, AuditLog, Property, Role, Room, User
from app.schemas.audit import AuditActor, AuditEntry, AuditPage, AuditQuery
from app.services.errors import InvalidInputError

Bounds = tuple[datetime | None, datetime | None]


# --- cursor --------------------------------------------------------------------------------


def encode_cursor(occurred_at: datetime, source: str, row_id: int) -> str:
    raw = json.dumps([occurred_at.isoformat(), source, row_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, source, row_id = json.loads(raw)
        if source not in ("change", "auth") or not isinstance(row_id, int):
            raise ValueError
        occurred_at = datetime.fromisoformat(at)
        if occurred_at.tzinfo is None:
            raise ValueError
        return occurred_at, source, row_id
    except ValueError, TypeError, binascii.Error, UnicodeDecodeError:
        raise InvalidInputError("The cursor is not valid.", code="invalid_cursor") from None


# --- the two sources -------------------------------------------------------------------------


def _in_range(
    column: ColumnElement[datetime] | InstrumentedAttribute[datetime], bounds: Bounds
) -> list[ColumnElement[bool]]:
    start, end = bounds
    conditions: list[ColumnElement[bool]] = []
    if start is not None:
        conditions.append(column >= start)
    if end is not None:
        conditions.append(column < end)
    return conditions


def _changes(query: AuditQuery, property_id: int, bounds: Bounds) -> Select[Any]:
    stmt = (
        select(
            literal("change").label("source"),
            AuditLog.id.label("row_id"),
            AuditLog.created_at.label("occurred_at"),
            AuditLog.action.label("action"),
            AuditLog.actor_user_id.label("actor_id"),
            User.username.label("actor_username"),
            User.full_name.label("actor_full_name"),
            AuditLog.target_type.label("target_type"),
            AuditLog.target_id.label("target_id"),
            AuditLog.before.label("before"),
            AuditLog.after.label("after"),
            cast(null(), JSONB).label("details"),
            AuditLog.ip.label("ip"),
        )
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        # System changes (no property) belong to every property's trail.
        .where(or_(AuditLog.property_id == property_id, AuditLog.property_id.is_(None)))
        .where(*_in_range(AuditLog.created_at, bounds))
    )
    if query.actor is not None:
        stmt = stmt.where(User.username == query.actor.strip().lower())
    if query.action is not None:
        stmt = stmt.where(AuditLog.action == query.action.value)
    if query.target_type is not None:
        stmt = stmt.where(AuditLog.target_type == query.target_type)
    if query.target_id is not None:
        stmt = stmt.where(AuditLog.target_id == query.target_id)
    return stmt


def _auth_events(query: AuditQuery, bounds: Bounds) -> Select[Any]:
    # Authentication is not per property yet (one property): every event is shown.
    # An event's actor and target are both the user who signed in (or tried to);
    # for an unknown username only the submitted name is known.
    username = func.coalesce(User.username, AuditEvent.username)
    user_id_text = cast(AuditEvent.user_id, String)
    stmt = (
        select(
            literal("auth").label("source"),
            AuditEvent.id.label("row_id"),
            AuditEvent.occurred_at.label("occurred_at"),
            AuditEvent.event_type.label("action"),
            AuditEvent.user_id.label("actor_id"),
            username.label("actor_username"),
            User.full_name.label("actor_full_name"),
            case((AuditEvent.user_id.is_not(None), literal("user"))).label("target_type"),
            user_id_text.label("target_id"),
            cast(null(), JSONB).label("before"),
            cast(null(), JSONB).label("after"),
            AuditEvent.details.label("details"),
            AuditEvent.ip_address.label("ip"),
        )
        .outerjoin(User, User.id == AuditEvent.user_id)
        .where(*_in_range(AuditEvent.occurred_at, bounds))
    )
    if query.actor is not None:
        stmt = stmt.where(username == query.actor.strip().lower())
    if query.action is not None:
        stmt = stmt.where(AuditEvent.event_type == query.action.value)
    if query.target_type is not None:  # only "user" reaches here (see _sources)
        stmt = stmt.where(AuditEvent.user_id.is_not(None))
    if query.target_id is not None:
        stmt = stmt.where(user_id_text == query.target_id)
    return stmt


def _sources(query: AuditQuery, property_id: int, bounds: Bounds) -> list[Select[Any]]:
    """Skip a source that cannot match (cheaper than filtering it to nothing)."""
    sources: list[Select[Any]] = []
    if not isinstance(query.action, AuditEventType):
        sources.append(_changes(query, property_id, bounds))
    if not isinstance(query.action, AuditAction) and query.target_type in (None, "user"):
        sources.append(_auth_events(query, bounds))
    return sources


async def _date_bounds(db: AsyncSession, property_id: int, query: AuditQuery) -> Bounds:
    """Inclusive calendar days in the property's time zone -> [start, end) instants."""
    if query.date_from is None and query.date_to is None:
        return None, None
    zone = ZoneInfo(
        await db.scalar(select(Property.time_zone).where(Property.id == property_id)) or "UTC"
    )

    def midnight(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=zone)

    start = midnight(query.date_from) if query.date_from else None
    end = midnight(query.date_to + timedelta(days=1)) if query.date_to else None
    return start, end


# --- the query -------------------------------------------------------------------------------


async def list_audit_entries(db: AsyncSession, property_id: int, query: AuditQuery) -> AuditPage:
    after = decode_cursor(query.cursor) if query.cursor else None
    sources = _sources(query, property_id, await _date_bounds(db, property_id, query))
    if not sources:
        return AuditPage(items=[], next_cursor=None)

    stream = (sources[0] if len(sources) == 1 else union_all(*sources)).subquery("entries")
    key = tuple_(stream.c.occurred_at, stream.c.source, stream.c.row_id)
    stmt = select(stream).order_by(
        stream.c.occurred_at.desc(), stream.c.source.desc(), stream.c.row_id.desc()
    )
    if after is not None:
        stmt = stmt.where(key < tuple_(*after))
    rows = (await db.execute(stmt.limit(query.limit + 1))).mappings().all()

    page = rows[: query.limit]
    next_cursor = (
        encode_cursor(page[-1]["occurred_at"], page[-1]["source"], page[-1]["row_id"])
        if len(rows) > query.limit
        else None
    )
    labels = await _target_labels(db, page)
    return AuditPage(
        items=[_entry(row, labels.get((row["target_type"], row["target_id"]))) for row in page],
        next_cursor=next_cursor,
    )


def _ids(rows: Sequence[Any], target_type: str) -> set[int]:
    return {
        int(row["target_id"])
        for row in rows
        if row["target_type"] == target_type and str(row["target_id"]).isdigit()
    }


async def _target_labels(db: AsyncSession, rows: Sequence[Any]) -> dict[tuple[str, str], str]:
    """Names for the page's targets: one query per target type present."""
    labels: dict[tuple[str, str], str] = {}
    # A role may be gone: fall back to the name the entry recorded.
    for row in rows:
        if row["target_type"] == "role":
            for snapshot in (row["after"], row["before"]):
                if isinstance(snapshot, dict) and isinstance(snapshot.get("name"), str):
                    labels[("role", row["target_id"])] = snapshot["name"]
                    break
    lookups: list[tuple[str, Any]] = [
        ("user", select(User.id, User.full_name).where(User.id.in_(_ids(rows, "user")))),
        ("role", select(Role.id, Role.name).where(Role.id.in_(_ids(rows, "role")))),
        ("room", select(Room.id, Room.number).where(Room.id.in_(_ids(rows, "room")))),
    ]
    for target_type, stmt in lookups:
        if not _ids(rows, target_type):
            continue
        for target_id, label in (await db.execute(stmt)).all():
            labels[(target_type, str(target_id))] = label
    return labels


def _entry(row: Any, target_label: str | None) -> AuditEntry:  # noqa: ANN401 - a result mapping
    actor = (
        AuditActor(
            id=row["actor_id"],
            username=row["actor_username"],
            full_name=row["actor_full_name"],
        )
        if row["actor_username"] is not None
        else None
    )
    return AuditEntry(
        id=f"{row['source']}-{row['row_id']}",
        source=row["source"],
        occurred_at=row["occurred_at"],
        action=row["action"],
        actor=actor,
        target_type=row["target_type"],
        target_id=row["target_id"],
        target_label=target_label,
        before=row["before"],
        after=row["after"],
        details=row["details"],
        ip=row["ip"],
    )
