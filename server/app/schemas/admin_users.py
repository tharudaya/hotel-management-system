"""Request and response models for /api/admin/users.

Responses never contain password hashes or session data.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.auth import PropertyRef, RoleRef

Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$"
    ),
]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# Deliberately simple: one @, no spaces, a dot in the domain.
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
JobTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
EmployeeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class UserStatus(StrEnum):
    """Derived, never stored. Precedence: deactivated > expired > invited > active."""

    ACTIVE = "active"
    INVITED = "invited"  # no password yet
    DEACTIVATED = "deactivated"
    EXPIRED = "expired"


UserSort = Literal[
    "name",
    "-name",
    "username",
    "-username",
    "email",
    "-email",
    "created_at",
    "-created_at",
    "last_login_at",
    "-last_login_at",
]


class UserListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str | None, Field(max_length=100)] = Field(
        default=None, description="Search name, username, email or employee id"
    )
    role_id: Annotated[int | None, Field(gt=0)] = Field(
        default=None, description="Role at the active property"
    )
    status: UserStatus | None = None
    sort: UserSort = "name"
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 25


class UserListItem(BaseModel):
    id: int
    username: str
    full_name: str
    email: str | None
    job_title: str | None
    employee_id: str | None
    status: UserStatus
    # Role at the active property.
    role: RoleRef | None
    last_login_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class UserListPage(BaseModel):
    items: list[UserListItem]
    total: int
    page: int
    page_size: int


class PropertyRole(BaseModel):
    property: PropertyRef
    role: RoleRef


# Actions on a user that the guard rules may forbid (see UserDetail.restrictions).
UserAction = Literal[
    "edit",
    "change_role",
    "deactivate",
    "reactivate",
    "revoke_sessions",
    "send_invite",
    "reset_password",
]


class UserRestrictions(BaseModel):
    """Why each action is not allowed for the signed-in user; null means allowed."""

    edit: str | None = None
    change_role: str | None = None
    deactivate: str | None = None
    reactivate: str | None = None
    revoke_sessions: str | None = None
    send_invite: str | None = None
    reset_password: str | None = None


class UserDetail(BaseModel):
    id: int
    username: str
    full_name: str
    email: str | None
    phone: str | None
    job_title: str | None
    employee_id: str | None
    status: UserStatus
    # One entry per property where the user has a role.
    roles: list[PropertyRole]
    created_at: datetime
    last_login_at: datetime | None
    expires_at: datetime | None
    deactivated_at: datetime | None
    # Actions the signed-in user may not take on this user right now, with the reason
    # (for disabling buttons). The server re-checks every request.
    restrictions: UserRestrictions


class AssignableRole(BaseModel):
    id: int
    name: str
    # The signed-in user may assign it (has user.manage and every permission it grants).
    assignable: bool


class UserCreate(BaseModel):
    """A new user starts invited: no password until they redeem an invite link."""

    model_config = ConfigDict(extra="forbid")

    username: Username
    full_name: FullName
    email: Email | None = None
    phone: Phone | None = None
    job_title: JobTitle | None = None
    employee_id: EmployeeId | None = None
    expires_at: AwareDatetime | None = None
    # Role at the active property, assigned in the same transaction (same rules as
    # PUT /{id}/role).
    role_id: Annotated[int | None, Field(gt=0)] = None


class UserUpdate(BaseModel):
    """Profile fields to change. Omitted fields stay as they are; null clears a field."""

    model_config = ConfigDict(extra="forbid")

    full_name: FullName | None = None
    email: Email | None = None
    phone: Phone | None = None
    job_title: JobTitle | None = None
    employee_id: EmployeeId | None = None
    expires_at: AwareDatetime | None = None

    @field_validator("full_name")
    @classmethod
    def _name_not_cleared(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("full_name cannot be cleared")
        return value


class RoleAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: Annotated[int, Field(gt=0)]


class SessionsRevoked(BaseModel):
    revoked_sessions: int
