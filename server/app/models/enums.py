from enum import StrEnum, unique


@unique
class UserRole(StrEnum):
    FRONT_DESK = "front_desk"
    HOUSEKEEPING = "housekeeping"
    MANAGER = "manager"
    ADMIN = "admin"


@unique
class HousekeepingStatus(StrEnum):
    DIRTY = "dirty"
    IN_PROGRESS = "in_progress"
    CLEAN = "clean"
    INSPECTED = "inspected"
    OUT_OF_ORDER = "out_of_order"


@unique
class ReservationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
