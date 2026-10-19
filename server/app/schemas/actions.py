from datetime import date

from pydantic import BaseModel, Field

from app.models import HousekeepingStatus


class CheckInRequest(BaseModel):
    room_id: int = Field(gt=0)
    # The room version the client last saw (from the dashboard entry).
    room_version: int = Field(ge=1)


class CheckOutRequest(BaseModel):
    room_version: int = Field(ge=1)


class HousekeepingStatusUpdate(BaseModel):
    status: HousekeepingStatus
    version: int = Field(ge=1)


class ErrorResponse(BaseModel):
    detail: str
    code: str


class NoAvailabilityResponse(ErrorResponse):
    """409 no_availability: the nights with no free room of the type."""

    unavailable_nights: list[date] | None = None
