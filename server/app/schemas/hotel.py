from datetime import date

from pydantic import BaseModel


class HotelInfo(BaseModel):
    name: str
    timezone: str
    # Today's date in the hotel's time zone.
    business_date: date
