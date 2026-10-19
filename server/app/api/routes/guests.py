from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import (
    ClientInfoDep,
    CurrentPermissions,
    CurrentPropertyId,
    require_permission,
)
from app.core.permissions import P
from app.db.session import SessionDep
from app.models import User
from app.schemas.actions import ErrorResponse
from app.schemas.guests import GuestCreate, GuestListQuery, GuestPage, GuestProfile, GuestUpdate
from app.services.guests import GuestService

router: APIRouter = APIRouter(prefix="/guests", tags=["guests"])

GuestViewer = Annotated[User, Depends(require_permission(P.GUEST_VIEW))]
# Guests are created and edited while booking.
GuestWriter = Annotated[User, Depends(require_permission(P.RESERVATION_CREATE))]
GuestId = Annotated[int, Path(gt=0)]
NOT_FOUND: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}


def _service(
    db: SessionDep,
    user: User,
    property_id: int,
    client: ClientInfoDep,
    permissions: CurrentPermissions,
) -> GuestService:
    return GuestService(db, user, property_id, client, P.GUEST_VIEW_CONTACT in permissions)


@router.get("")
async def search_guests(
    query: Annotated[GuestListQuery, Query()],
    db: SessionDep,
    user: GuestViewer,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    permissions: CurrentPermissions,
) -> GuestPage:
    """Guests whose name (and, with guest.view_contact, email or phone) contains every
    word of `q`, by last name. Contact details only with guest.view_contact."""
    return await _service(db, user, property_id, client, permissions).search(query)


@router.get("/{guest_id}", responses=NOT_FOUND)
async def get_guest(
    guest_id: GuestId,
    db: SessionDep,
    user: GuestViewer,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    permissions: CurrentPermissions,
) -> GuestProfile:
    return await _service(db, user, property_id, client, permissions).get(guest_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_guest(
    body: GuestCreate,
    db: SessionDep,
    user: GuestWriter,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    permissions: CurrentPermissions,
) -> GuestProfile:
    return await _service(db, user, property_id, client, permissions).create(body)


@router.patch("/{guest_id}", responses=NOT_FOUND)
async def update_guest(
    guest_id: GuestId,
    body: GuestUpdate,
    db: SessionDep,
    user: GuestWriter,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    permissions: CurrentPermissions,
) -> GuestProfile:
    """Only the fields sent change; null clears email, phone or notes."""
    return await _service(db, user, property_id, client, permissions).update(guest_id, body)
