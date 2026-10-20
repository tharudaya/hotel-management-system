from fastapi import APIRouter

from app.api.deps import CurrentPropertyId, CurrentUser, HotelTodayDep, SettingsDep
from app.db.session import SessionDep
from app.models import Property
from app.schemas.hotel import HotelInfo

router: APIRouter = APIRouter(prefix="/hotel", tags=["hotel"])


@router.get("")
async def hotel(
    user: CurrentUser,
    settings: SettingsDep,
    today: HotelTodayDep,
    db: SessionDep,
    property_id: CurrentPropertyId,
) -> HotelInfo:
    """Hotel name, today's business date and the currency (for the app header and price
    fields). Any signed-in user."""
    prop = await db.get_one(Property, property_id)
    return HotelInfo(
        name=settings.hotel_name,
        timezone=settings.hotel_timezone,
        business_date=today,
        currency=prop.currency,
    )
