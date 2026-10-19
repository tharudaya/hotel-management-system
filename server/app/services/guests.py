"""Guest profiles of one property (see "Reservations" in CLAUDE.md).

Privacy:
- Email and phone are returned only with guest.view_contact (`include_contact`); without
  it they are null and `contact_hidden` is true. Searching by email or phone also needs
  it, so the search cannot be used to confirm someone's contact details.
- Audit rows name the contact fields that changed, never their values.
- Nothing here logs; never pass guest data to a logger.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, Guest, User
from app.schemas.guests import GuestCreate, GuestListQuery, GuestPage, GuestProfile, GuestUpdate
from app.services.audit import ClientInfo, record_change
from app.services.errors import NotFoundError

CONTACT_FIELDS: tuple[str, ...] = ("email", "phone")
PROFILE_FIELDS: tuple[str, ...] = ("first_name", "last_name", "notes")


def guest_profile(guest: Guest, *, include_contact: bool) -> GuestProfile:
    return GuestProfile(
        id=guest.id,
        first_name=guest.first_name,
        last_name=guest.last_name,
        email=guest.email if include_contact else None,
        phone=guest.phone if include_contact else None,
        contact_hidden=not include_contact,
        notes=guest.notes,
        created_at=guest.created_at,
        updated_at=guest.updated_at,
    )


@dataclass
class GuestService:
    """Acts on behalf of `actor` at one property. Construct per request."""

    db: AsyncSession
    actor: User
    property_id: int
    client: ClientInfo
    include_contact: bool

    # --- reads -----------------------------------------------------------------------

    async def search(self, query: GuestListQuery) -> GuestPage:
        criteria: list[ColumnElement[bool]] = [Guest.property_id == self.property_id]
        criteria += self._matches(query.q or "")
        total = await self.db.scalar(select(func.count()).select_from(Guest).where(*criteria))
        guests = (
            await self.db.scalars(
                select(Guest)
                .where(*criteria)
                .order_by(Guest.last_name, Guest.first_name, Guest.id)
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
        ).all()
        return GuestPage(
            items=[self._out(g) for g in guests],
            total=total or 0,
            page=query.page,
            page_size=query.page_size,
        )

    async def get(self, guest_id: int) -> GuestProfile:
        return self._out(await self.load(guest_id))

    async def load(self, guest_id: int) -> Guest:
        guest = await self.db.scalar(
            select(Guest).where(Guest.id == guest_id, Guest.property_id == self.property_id)
        )
        if guest is None:
            raise NotFoundError(f"Guest {guest_id} not found", code="guest_not_found")
        return guest

    # --- writes ----------------------------------------------------------------------

    async def create(self, data: GuestCreate) -> GuestProfile:
        guest = await self.add(data)
        await self.db.commit()
        await self.db.refresh(guest)
        return self._out(guest)

    async def add(self, data: GuestCreate) -> Guest:
        """Create the guest and its audit row in the current transaction (no commit), for
        callers that create a guest as part of a larger change (a booking)."""
        guest = Guest(property_id=self.property_id, **data.model_dump())
        self.db.add(guest)
        await self.db.flush()
        self._audit(
            AuditAction.GUEST_CREATED,
            guest.id,
            before=None,
            after=self._snapshot(guest, PROFILE_FIELDS, set(CONTACT_FIELDS)),
        )
        return guest

    async def update(self, guest_id: int, data: GuestUpdate) -> GuestProfile:
        guest = await self.load(guest_id)
        changes = {
            field: getattr(data, field)
            for field in data.model_fields_set
            if getattr(guest, field) != getattr(data, field)
        }
        if changes:
            profile = [f for f in PROFILE_FIELDS if f in changes]
            contact = {f for f in CONTACT_FIELDS if f in changes}
            before = self._snapshot(guest, profile, contact)
            for field, value in changes.items():
                setattr(guest, field, value)
            self._audit(
                AuditAction.GUEST_UPDATED,
                guest.id,
                before=before,
                after=self._snapshot(guest, profile, contact),
            )
            await self.db.commit()
            await self.db.refresh(guest)
        return self._out(guest)

    # --- helpers ---------------------------------------------------------------------

    def _out(self, guest: Guest) -> GuestProfile:
        return guest_profile(guest, include_contact=self.include_contact)

    def _matches(self, q: str) -> list[ColumnElement[bool]]:
        """Every word must match. Words with digits and no letters are compared as
        phone digits ("70-123" finds "+4670123..."). Name-only without contact access."""
        searched = (
            Guest.search_text
            if self.include_contact
            else func.lower(Guest.first_name + " " + Guest.last_name)
        )
        criteria: list[ColumnElement[bool]] = []
        for word in q.lower().split():
            if re.search(r"\d", word) and not re.search(r"[^\W\d_]", word):
                word = re.sub(r"\D", "", word)
                if not word:
                    continue
            criteria.append(searched.contains(word, autoescape=True))
        return [and_(*criteria)] if criteria else []

    @staticmethod
    def _snapshot(guest: Guest, profile: Iterable[str], contact: set[str]) -> dict[str, object]:
        """Profile fields with their values; contact fields only by name."""
        snapshot: dict[str, object] = {field: getattr(guest, field) for field in profile}
        if contact:
            snapshot["contact"] = {
                field: "set" if getattr(guest, field) else "empty" for field in sorted(contact)
            }
        return snapshot

    def _audit(
        self,
        action: AuditAction,
        guest_id: int,
        *,
        before: dict[str, object] | None,
        after: dict[str, object],
    ) -> None:
        record_change(
            self.db,
            action=action,
            target_type="guest",
            target_id=guest_id,
            actor=self.actor,
            property_id=self.property_id,
            client=self.client,
            before=before,
            after=after,
        )
