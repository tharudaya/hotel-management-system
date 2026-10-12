from httpx import AsyncClient

from app.core.config import Settings


async def test_health_ok_when_database_reachable(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_503_when_database_unreachable(client_without_database: AsyncClient) -> None:
    response = await client_without_database.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}


async def test_cors_allows_configured_origin_with_credentials(
    client: AsyncClient, test_settings: Settings
) -> None:
    origin = test_settings.cors_origins[0]

    response = await client.get("/api/health", headers={"Origin": origin})

    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_cors_rejects_unknown_origin(client: AsyncClient) -> None:
    response = await client.get("/api/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers
