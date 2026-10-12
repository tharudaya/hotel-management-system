"""User administration endpoints (thin: the rules live in services/user_admin.py)."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import (
    ClientInfoDep,
    CurrentPermissions,
    CurrentPropertyId,
    EmailSenderDep,
    EventBusDep,
    SettingsDep,
    require_permission,
)
from app.core.permissions import P
from app.db.session import SessionDep
from app.models import User
from app.schemas.account import LinkIssued
from app.schemas.actions import ErrorResponse
from app.schemas.admin_users import (
    AssignableRole,
    RoleAssignment,
    SessionsRevoked,
    UserCreate,
    UserDetail,
    UserListPage,
    UserListQuery,
    UserUpdate,
)
from app.services.user_admin import UserAdminService

router: APIRouter = APIRouter(prefix="/admin/users", tags=["admin: users"])

UserId = Annotated[int, Path(gt=0)]
ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
}


def _service_for(permission: str) -> Callable[..., Awaitable[UserAdminService]]:
    """Dependency building a UserAdminService for a user holding `permission`."""

    async def build(
        actor: Annotated[User, Depends(require_permission(permission))],
        permissions: CurrentPermissions,
        db: SessionDep,
        bus: EventBusDep,
        property_id: CurrentPropertyId,
        client: ClientInfoDep,
        settings: SettingsDep,
        email_sender: EmailSenderDep,
    ) -> UserAdminService:
        return UserAdminService(
            db=db,
            bus=bus,
            actor=actor,
            actor_permissions=permissions,
            property_id=property_id,
            client=client,
            settings=settings,
            email_sender=email_sender,
        )

    return build


Viewer = Annotated[UserAdminService, Depends(_service_for(P.USER_VIEW))]
Manager = Annotated[UserAdminService, Depends(_service_for(P.USER_MANAGE))]


@router.get("")
async def list_users(service: Viewer, query: Annotated[UserListQuery, Query()]) -> UserListPage:
    """Search, filter (role at this property, status), sort and paginate users."""
    return await service.list_users(query)


@router.get("/roles")
async def list_assignable_roles(service: Viewer) -> list[AssignableRole]:
    """Roles at this property for filtering and assignment; `assignable` marks the ones
    you may give (you need user.manage and every permission the role grants)."""
    return await service.list_assignable_roles()


@router.get("/{user_id}", responses=ERRORS)
async def get_user(user_id: UserId, service: Viewer) -> UserDetail:
    return await service.get_user(user_id)


@router.post("", status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def create_user(body: UserCreate, service: Manager) -> UserDetail:
    """Create an invited user (no password yet)."""
    return await service.create_user(body)


@router.patch("/{user_id}", responses=ERRORS)
async def update_user(user_id: UserId, body: UserUpdate, service: Manager) -> UserDetail:
    return await service.update_user(user_id, body)


@router.put("/{user_id}/role", responses=ERRORS)
async def assign_role(user_id: UserId, body: RoleAssignment, service: Manager) -> UserDetail:
    """Assign or change the user's role at the active property."""
    return await service.assign_role(user_id, body.role_id)


@router.post("/{user_id}/deactivate", responses=ERRORS)
async def deactivate_user(user_id: UserId, service: Manager) -> UserDetail:
    return await service.deactivate(user_id)


@router.post("/{user_id}/reactivate", responses=ERRORS)
async def reactivate_user(user_id: UserId, service: Manager) -> UserDetail:
    return await service.reactivate(user_id)


@router.post("/{user_id}/revoke-sessions", responses=ERRORS)
async def revoke_sessions(user_id: UserId, service: Manager) -> SessionsRevoked:
    """Log the user out everywhere (all sessions and WebSockets)."""
    return await service.revoke_sessions(user_id)


@router.post("/{user_id}/invite", responses=ERRORS)
async def send_invite(user_id: UserId, service: Manager) -> LinkIssued:
    """Email an invite link (72 h), or return it once if the user has no email.

    Re-sending expires the previous link.
    """
    return await service.send_invite(user_id)


@router.post("/{user_id}/reset-password", responses=ERRORS)
async def send_password_reset(user_id: UserId, service: Manager) -> LinkIssued:
    """Email a password reset link (1 h), or return it once if the user has no email."""
    return await service.send_password_reset(user_id)
