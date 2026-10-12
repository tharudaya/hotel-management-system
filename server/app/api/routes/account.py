"""Public account endpoints: activate an invite, reset a password, forgot password.

No session needed. Links carry a single-use token; see services/account_tokens.py.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, status

from app.api.deps import ClientInfoDep, EmailSenderDep, EventBusDep, SettingsDep
from app.db.session import SessionDep
from app.models import TokenPurpose
from app.schemas.account import ForgotPasswordRequest, Message, SetPasswordRequest, TokenInfo
from app.schemas.actions import ErrorResponse
from app.services import account_tokens

router: APIRouter = APIRouter(prefix="/auth", tags=["account"])

Token = Annotated[str, Path(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
TOKEN_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Unknown link"},
    status.HTTP_410_GONE: {"model": ErrorResponse, "description": "Link expired or used"},
}
SET_PASSWORD_ERRORS: dict[int | str, dict[str, object]] = {
    **TOKEN_ERRORS,
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "Password breaks a rule",
    },
}


@router.get("/activate/{token}", responses=TOKEN_ERRORS)
async def check_invite(token: Token, db: SessionDep, settings: SettingsDep) -> TokenInfo:
    """Who the invite is for (username and name only)."""
    return await account_tokens.token_info(db, settings, token=token, purpose=TokenPurpose.INVITE)


@router.post("/activate/{token}", responses=SET_PASSWORD_ERRORS)
async def activate(
    token: Token,
    body: SetPasswordRequest,
    db: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
    client: ClientInfoDep,
) -> Message:
    """Set the first password and activate the account. Does not log in."""
    await account_tokens.redeem(
        db,
        settings,
        bus,
        token=token,
        purpose=TokenPurpose.INVITE,
        password=body.password,
        client=client,
    )
    return Message(detail="Your account is ready. You can now sign in.")


@router.get("/reset-password/{token}", responses=TOKEN_ERRORS)
async def check_reset(token: Token, db: SessionDep, settings: SettingsDep) -> TokenInfo:
    return await account_tokens.token_info(
        db, settings, token=token, purpose=TokenPurpose.PASSWORD_RESET
    )


@router.post("/reset-password/{token}", responses=SET_PASSWORD_ERRORS)
async def reset_password(
    token: Token,
    body: SetPasswordRequest,
    db: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
    client: ClientInfoDep,
) -> Message:
    """Set a new password; every existing session of the user ends. Does not log in."""
    await account_tokens.redeem(
        db,
        settings,
        bus,
        token=token,
        purpose=TokenPurpose.PASSWORD_RESET,
        password=body.password,
        client=client,
    )
    return Message(detail="Your password has been changed. You can now sign in.")


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    body: ForgotPasswordRequest,
    background: BackgroundTasks,
    db: SessionDep,
    settings: SettingsDep,
    email_sender: EmailSenderDep,
    client: ClientInfoDep,
) -> Message:
    """Always the same answer, whether or not the account exists.

    The email is sent after the response, so timing does not reveal it either.
    """
    pending = await account_tokens.request_password_reset(
        db, settings, email=body.email, client=client
    )
    if pending is not None:
        background.add_task(account_tokens.deliver, email_sender, pending.message)
    return Message(detail=account_tokens.FORGOT_RESPONSE)
