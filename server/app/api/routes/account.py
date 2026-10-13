"""Public account endpoints: activate an invite, reset a password, forgot password.

No session needed. Links carry a single-use token, sent in the request body (never in
the URL path, which would land in access logs); see services/account_tokens.py.
"""

from fastapi import APIRouter, BackgroundTasks, status

from app.api.deps import ClientInfoDep, EmailSenderDep, EventBusDep, SettingsDep
from app.db.session import SessionDep
from app.models import TokenPurpose
from app.schemas.account import (
    ForgotPasswordRequest,
    Message,
    SetPasswordRequest,
    TokenInfo,
    TokenRequest,
)
from app.schemas.actions import ErrorResponse
from app.services import account_tokens

router: APIRouter = APIRouter(prefix="/auth", tags=["account"])

TOKEN_ERRORS: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Unknown link"},
    status.HTTP_410_GONE: {"model": ErrorResponse, "description": "Link expired or used"},
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": ErrorResponse,
        "description": "Too many rejected links from this address",
    },
}
SET_PASSWORD_ERRORS: dict[int | str, dict[str, object]] = {
    **TOKEN_ERRORS,
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "Password breaks a rule",
    },
}


@router.post("/activate/check", responses=TOKEN_ERRORS)
async def check_invite(
    body: TokenRequest, db: SessionDep, settings: SettingsDep, client: ClientInfoDep
) -> TokenInfo:
    """Who the invite is for (username and name only)."""
    return await account_tokens.token_info(
        db, settings, token=body.token, purpose=TokenPurpose.INVITE, client=client
    )


@router.post("/activate", responses=SET_PASSWORD_ERRORS)
async def activate(
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
        token=body.token,
        purpose=TokenPurpose.INVITE,
        password=body.password,
        client=client,
    )
    return Message(detail="Your account is ready. You can now sign in.")


@router.post("/reset-password/check", responses=TOKEN_ERRORS)
async def check_reset(
    body: TokenRequest, db: SessionDep, settings: SettingsDep, client: ClientInfoDep
) -> TokenInfo:
    return await account_tokens.token_info(
        db, settings, token=body.token, purpose=TokenPurpose.PASSWORD_RESET, client=client
    )


@router.post("/reset-password", responses=SET_PASSWORD_ERRORS)
async def reset_password(
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
        token=body.token,
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
