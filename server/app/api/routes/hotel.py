from fastapi import APIRouter

from app.api.deps import CurrentUser, HotelTodayDep, SettingsDep
from app.schemas.hotel import HotelInfo

router: APIRouter = APIRouter(prefix="/hotel", tags=["hotel"])


@router.get("")
async def hotel(user: CurrentUser, settings: SettingsDep, today: HotelTodayDep) -> HotelInfo:
    """Hotel name and today's business date (for the app header). Any signed-in user."""
    return HotelInfo(
        name=settings.hotel_name, timezone=settings.hotel_timezone, business_date=today
    )
