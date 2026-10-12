"""Role administration endpoints (thin: the rules live in services/role_admin.py)."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import (
    ClientInfoDep,
    CurrentPermissions,
    CurrentPropertyId,
    EventBusDep,
    require_permission,
)
from app.core.permissions import P
from app.db.session import SessionDep
from app.models import User
from app.schemas.actions import ErrorResponse
from app.schemas.admin_roles import (
    PermissionGroup,
    RoleCreate,
    RoleDetail,
    RoleSummary,
    RoleUpdate,
)
from app.services.role_admin import RoleAdminService, permission_catalog

router: APIRouter = APIRouter(prefix="/admin/roles", tags=["admin: roles"])

RoleId = Annotated[int, Path(gt=0)]
ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
}


def _service_for(permission: str) -> Callable[..., Awaitable[RoleAdminService]]:
    """Dependency building a RoleAdminService for a user holding `permission`."""

    async def build(
        actor: Annotated[User, Depends(require_permission(permission))],
        permissions: CurrentPermissions,
        db: SessionDep,
        bus: EventBusDep,
        property_id: CurrentPropertyId,
        client: ClientInfoDep,
    ) -> RoleAdminService:
        return RoleAdminService(
            db=db,
            bus=bus,
            actor=actor,
            actor_permissions=permissions,
            property_id=property_id,
            client=client,
        )

    return build


Viewer = Annotated[RoleAdminService, Depends(_service_for(P.ROLE_VIEW))]
Manager = Annotated[RoleAdminService, Depends(_service_for(P.ROLE_MANAGE))]


@router.get("")
async def list_roles(service: Viewer) -> list[RoleSummary]:
    """Roles at this property with user and permission counts (system roles first)."""
    return await service.list_roles()


@router.get("/permissions", dependencies=[Depends(require_permission(P.ROLE_VIEW))])
async def list_permissions() -> list[PermissionGroup]:
    """The permission catalog grouped by area, with descriptions."""
    return permission_catalog()


@router.get("/{role_id}", responses=ERRORS)
async def get_role(role_id: RoleId, service: Viewer) -> RoleDetail:
    """The role with its permission keys and the users who have it."""
    return await service.get_role(role_id)


@router.post("", status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def create_role(data: RoleCreate, service: Manager) -> RoleDetail:
    """Create a custom role, optionally starting from another role's permissions."""
    return await service.create_role(data)


@router.patch("/{role_id}", responses=ERRORS)
async def update_role(role_id: RoleId, data: RoleUpdate, service: Manager) -> RoleDetail:
    """Change name, description and/or permissions (the list replaces the whole set)."""
    return await service.update_role(role_id, data)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def delete_role(role_id: RoleId, service: Manager) -> None:
    """Delete a custom role nobody has."""
    await service.delete_role(role_id)
