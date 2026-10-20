from datetime import date

from pydantic import BaseModel


class HotelInfo(BaseModel):
    name: str
    timezone: str
    # Today's date in the hotel's time zone.
    business_date: date
    # ISO 4217 code of the property's currency; prices are minor units of it.
    currency: str
