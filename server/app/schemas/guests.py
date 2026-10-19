import re
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

GuestName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
GuestNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


def _normalise_email(value: str) -> str:
    return value.lower()


# Deliberately simple (as for staff accounts): one @, no spaces, a dot in the domain.
# Stored lower-case (the database checks it).
GuestEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    AfterValidator(_normalise_email),
]

PHONE_CHARS = re.compile(r"^\+?[0-9 ()./-]+$")


def _normalise_phone(value: str) -> str:
    """Keep a leading + and the digits: "+46 (0)70-123 45 67" -> "+460701234567"."""
    if not PHONE_CHARS.fullmatch(value):
        raise ValueError("Use digits, spaces, + ( ) . / - only")
    digits = re.sub(r"\D", "", value)
    if not 5 <= len(digits) <= 20:
        raise ValueError("A phone number has 5 to 20 digits")
    return ("+" if value.startswith("+") else "") + digits


GuestPhone = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    AfterValidator(_normalise_phone),
]


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


class GuestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: GuestName
    last_name: GuestName
    email: GuestEmail | None = None
    phone: GuestPhone | None = None
    notes: GuestNotes | None = None

    @field_validator("email", "phone", "notes", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return _blank_to_none(value)


class GuestUpdate(BaseModel):
    """Only the fields sent change; null clears email, phone or notes."""

    model_config = ConfigDict(extra="forbid")

    first_name: GuestName | None = None
    last_name: GuestName | None = None
    email: GuestEmail | None = None
    phone: GuestPhone | None = None
    notes: GuestNotes | None = None

    @field_validator("email", "phone", "notes", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def _names_not_cleared(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Names cannot be cleared")
        return value


class GuestProfile(BaseModel):
    id: int
    first_name: str
    last_name: str
    # Contact details need guest.view_contact; without it they are null and
    # contact_hidden is true (so "no email" and "not shown" can be told apart).
    email: str | None
    phone: str | None
    contact_hidden: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class GuestListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str | None, Field(max_length=100)] = Field(
        default=None,
        description="Words of the name; email and phone too with guest.view_contact",
    )
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 25


class GuestPage(BaseModel):
    items: list[GuestProfile]
    total: int
    page: int
    page_size: int
