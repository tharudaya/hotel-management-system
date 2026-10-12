from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import (
    ClientInfoDep,
    CurrentUser,
    EventBusDep,
    SessionTokenDep,
    SettingsDep,
)
from app.core.config import Settings
from app.db.session import SessionDep
from app.schemas.auth import LoginRequest, UserOut
from app.services import auth as auth_service

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS: str = "Invalid username or password"


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post(
    "/login",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": INVALID_CREDENTIALS},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many failed attempts"},
    },
)
async def login(
    body: LoginRequest,
    response: Response,
    db: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
    client: ClientInfoDep,
    previous_token: SessionTokenDep,
) -> UserOut:
    try:
        result = await auth_service.login(
            db,
            settings,
            bus,
            username=body.username,
            password=body.password,
            client=client,
            previous_token=previous_token,
        )
    except auth_service.TooManyAttemptsError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
    except auth_service.InvalidCredentialsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS) from None

    _set_session_cookie(response, settings, result.token)
    return UserOut.model_validate(result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: SessionDep,
    settings: SettingsDep,
    bus: EventBusDep,
    client: ClientInfoDep,
    token: SessionTokenDep,
) -> None:
    # Idempotent: succeeds (and clears the cookie) even without a valid session.
    if token:
        await auth_service.logout(db, settings, bus, token=token, client=client)
    _clear_session_cookie(response, settings)


@router.get("/me", responses={status.HTTP_401_UNAUTHORIZED: {"description": "Not authenticated"}})
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
