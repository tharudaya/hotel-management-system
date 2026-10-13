"""Room setup endpoints (thin: the rules live in services/room_setup.py).

Everything here needs room.manage (Admin only by default).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import (
    ClientInfoDep,
    CurrentPropertyId,
    EventBusDep,
    HotelTodayDep,
    require_permission,
)
from app.core.permissions import P
from app.db.session import SessionDep
from app.models import User
from app.schemas.actions import ErrorResponse
from app.schemas.room_setup import (
    RoomCreate,
    RoomListQuery,
    RoomOut,
    RoomTypeCreate,
    RoomTypeListQuery,
    RoomTypeOut,
    RoomTypeUpdate,
    RoomUpdate,
    RoomVersion,
)
from app.services.room_setup import RoomSetupService

ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
}


async def room_setup(
    actor: Annotated[User, Depends(require_permission(P.ROOM_MANAGE))],
    db: SessionDep,
    bus: EventBusDep,
    property_id: CurrentPropertyId,
    client: ClientInfoDep,
    today: HotelTodayDep,
) -> RoomSetupService:
    return RoomSetupService(
        db=db, bus=bus, actor=actor, property_id=property_id, client=client, today=today
    )


Service = Annotated[RoomSetupService, Depends(room_setup)]
Id = Annotated[int, Path(gt=0)]

# --- room types ------------------------------------------------------------------------

room_types: APIRouter = APIRouter(prefix="/admin/room-types", tags=["admin: room setup"])


@room_types.get("")
async def list_room_types(
    service: Service, query: Annotated[RoomTypeListQuery, Query()]
) -> list[RoomTypeOut]:
    """Room types with their number of active rooms; retired ones only on request."""
    return await service.list_room_types(query)


@room_types.post("", status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def create_room_type(data: RoomTypeCreate, service: Service) -> RoomTypeOut:
    return await service.create_room_type(data)


@room_types.patch("/{room_type_id}", responses=ERRORS)
async def update_room_type(room_type_id: Id, data: RoomTypeUpdate, service: Service) -> RoomTypeOut:
    return await service.update_room_type(room_type_id, data)


@room_types.post("/{room_type_id}/retire", responses=ERRORS)
async def retire_room_type(room_type_id: Id, service: Service) -> RoomTypeOut:
    """Refused while the type has active rooms or upcoming reservations."""
    return await service.retire_room_type(room_type_id)


@room_types.post("/{room_type_id}/reactivate", responses=ERRORS)
async def reactivate_room_type(room_type_id: Id, service: Service) -> RoomTypeOut:
    return await service.reactivate_room_type(room_type_id)


# --- rooms -------------------------------------------------------------------------------

rooms: APIRouter = APIRouter(prefix="/admin/rooms", tags=["admin: room setup"])


@rooms.get("")
async def list_rooms(service: Service, query: Annotated[RoomListQuery, Query()]) -> list[RoomOut]:
    """Rooms sorted by number; filter by floor, room type and active/retired."""
    return await service.list_rooms(query)


@rooms.post("", status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def create_room(data: RoomCreate, service: Service) -> RoomOut:
    """New rooms start with housekeeping status inspected (Ready)."""
    return await service.create_room(data)


@rooms.patch("/{room_id}", responses=ERRORS)
async def update_room(room_id: Id, data: RoomUpdate, service: Service) -> RoomOut:
    """Send the version you saw; 409 if someone else changed the room meanwhile."""
    return await service.update_room(room_id, data)


@rooms.post("/{room_id}/retire", responses=ERRORS)
async def retire_room(room_id: Id, data: RoomVersion, service: Service) -> RoomOut:
    """Refused while a guest is checked in or upcoming reservations are assigned."""
    return await service.retire_room(room_id, data.version)


@rooms.post("/{room_id}/reactivate", responses=ERRORS)
async def reactivate_room(room_id: Id, data: RoomVersion, service: Service) -> RoomOut:
    return await service.reactivate_room(room_id, data.version)
