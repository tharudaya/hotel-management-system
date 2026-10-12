from fastapi import APIRouter, Response, status

from app.db.session import SessionDep
from app.schemas.health import HealthResponse
from app.services.health import is_database_available

router: APIRouter = APIRouter(tags=["health"])


@router.get(
    "/health",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(session: SessionDep, response: Response) -> HealthResponse:
    if await is_database_available(session):
        return HealthResponse(status="ok", database="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="error", database="unavailable")
