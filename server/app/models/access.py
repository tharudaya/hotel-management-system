"""Properties, roles and permissions (see "Users and permissions" in CLAUDE.md)."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Property(Base):
    """A hotel. Roles and role assignments are per property."""

    __tablename__ = "properties"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso_code"),
        CheckConstraint("max_stay_nights BETWEEN 1 AND 365", name="max_stay_nights_range"),
        CheckConstraint(
            "booking_horizon_days BETWEEN 1 AND 1825", name="booking_horizon_days_range"
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # IANA zone, e.g. "Europe/Stockholm".
    time_zone: Mapped[str] = mapped_column(String(64))
    # ISO 4217 code; prices are integer minor units of this currency (öre for SEK).
    currency: Mapped[str] = mapped_column(String(3), server_default="SEK")
    # Booking limits: longest stay, and how far ahead a stay may start.
    max_stay_nights: Mapped[int] = mapped_column(SmallInteger, server_default="30")
    booking_horizon_days: Mapped[int] = mapped_column(SmallInteger, server_default="730")

    def __repr__(self) -> str:
        return f"<Property {self.name!r}>"


class Permission(Base):
    """Mirror of the code catalog (app/core/permissions.py), synced on startup."""

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(String(255))
    group: Mapped[str] = mapped_column(String(50))


class Role(TimestampMixin, Base):
    """A named bundle of permissions within one property."""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("property_id", "name", name="uq_roles_property_id_name"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    # Indexed by the (property_id, name) unique constraint.
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    # Created by the system for every property; cannot be deleted.
    is_system: Mapped[bool] = mapped_column(default=False, server_default=false())

    property: Mapped[Property] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"<Role {self.name!r} property={self.property_id}>"


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    # Removing a key from the catalog deletes it from every role.
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permissions.key", ondelete="CASCADE"), primary_key=True, index=True
    )


class UserPropertyRole(Base):
    """A user's role at one property. One role per user per property (for now)."""

    __tablename__ = "user_property_roles"

    # The primary key enforces one role per user per property.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # RESTRICT: a role that is still assigned cannot be deleted.
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), index=True)
    # Null for assignments made by the system (migration, seed).
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    role: Mapped[Role] = relationship(lazy="raise")
