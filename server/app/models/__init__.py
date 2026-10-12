"""SQLAlchemy models.

Import every model module here so that `Base.metadata` is complete when
Alembic autogenerates migrations.
"""

from app.models.auth import AuditEvent, AuditEventType, UserSession
from app.models.base import Base
from app.models.enums import HousekeepingStatus, ReservationStatus, UserRole
from app.models.guest import Guest
from app.models.reservation import Reservation
from app.models.room import Room, RoomType
from app.models.user import User

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "Base",
    "Guest",
    "HousekeepingStatus",
    "Reservation",
    "ReservationStatus",
    "Room",
    "RoomType",
    "User",
    "UserRole",
    "UserSession",
]
