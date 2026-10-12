"""Request and response models for /api/admin/roles."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.admin_users import UserStatus

RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RoleDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
# Checked against the catalog by the service (unknown keys are a 422 with the list).
PermissionKeys = Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=200)]


class CatalogPermission(BaseModel):
    key: str
    description: str


class PermissionGroup(BaseModel):
    """One area of the catalog (users, rooms, reservations, ...), in catalog order."""

    group: str
    permissions: list[CatalogPermission]


class RoleSummary(BaseModel):
    id: int
    name: str
    description: str | None
    # Created for every property. Cannot be deleted or renamed.
    is_system: bool
    # The Admin role: always every permission, cannot be changed at all.
    is_admin: bool
    user_count: int
    permission_count: int
    # For the signed-in user (UI hints only; the server checks every request).
    can_edit: bool
    can_delete: bool


class RoleUser(BaseModel):
    id: int
    username: str
    full_name: str
    status: UserStatus


class RoleDetail(RoleSummary):
    # Sorted. For the Admin role: the whole catalog.
    permissions: list[str]
    users: list[RoleUser]


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RoleName
    description: RoleDescription | None = None
    permissions: PermissionKeys = Field(default_factory=list)
    # Start from this role's permissions (and description, if none is given); keys in
    # `permissions` are added to them.
    copy_from_role_id: Annotated[int | None, Field(gt=0)] = None


class RoleUpdate(BaseModel):
    """Only the fields sent are changed. `permissions` replaces the whole set."""

    model_config = ConfigDict(extra="forbid")

    name: RoleName | None = None
    description: RoleDescription | None = None
    permissions: PermissionKeys | None = None
