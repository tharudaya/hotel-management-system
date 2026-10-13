"""Invite activation, password reset and forgot-password."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.admin_users import Email


class TokenInfo(BaseModel):
    """What a valid link reveals: who it is for. Nothing else."""

    username: str
    full_name: str


# 256-bit tokens from secrets.token_urlsafe (43 chars); bounds reject junk early.
LinkToken = Annotated[
    str, StringConstraints(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]


class TokenRequest(BaseModel):
    """Tokens travel in request bodies, never in URLs (which end up in access logs)."""

    model_config = ConfigDict(extra="forbid")

    token: LinkToken


class SetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: LinkToken
    # Rules (length, common passwords, username) are checked by the service, with
    # specific error codes; the bounds here only reject absurd input early.
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email


class Message(BaseModel):
    detail: str


class LinkIssued(BaseModel):
    """Result of an admin issuing an invite or reset link."""

    # "email": sent to the user's address. "link": the user has no email address, so
    # the admin must pass the link on; it is shown only this once.
    delivery: Literal["email", "link"]
    sent_to: str | None
    link: str | None
    expires_at: datetime
