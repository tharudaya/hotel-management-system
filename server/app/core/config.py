"""Application settings, loaded from environment variables and .env files."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SERVER_DIR: Path = Path(__file__).resolve().parents[2]
REPO_ROOT: Path = SERVER_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win. Real environment variables override both.
        env_file=(REPO_ROOT / ".env", SERVER_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # IANA zone of the hotel. "Today" (arrivals, departures, nights) is the
    # date in this zone, never the server clock's.
    hotel_timezone: str = "Europe/Stockholm"

    database_url: str
    # Separate database used by the test suite; never point this at real data.
    test_database_url: str | None = None

    session_secret: SecretStr
    session_cookie_name: str = "hotel_session"
    session_max_age_seconds: int = 8 * 60 * 60
    session_cookie_secure: bool = True

    # Login throttling: failed attempts counted over a sliding window.
    login_failure_window_seconds: int = 15 * 60
    login_max_failures_per_username: int = 5
    login_max_failures_per_ip: int = 20

    # WebSocket: the server pings this often and closes sockets that have not
    # answered for two intervals; the session is re-validated on each ping.
    ws_heartbeat_seconds: float = 25.0
    # Messages queued for one slow client before it is disconnected.
    ws_max_pending_messages: int = 100

    # Comma-separated in the environment, e.g. "http://localhost:5173,http://127.0.0.1:5173".
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("hotel_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @property
    def hotel_tz(self) -> ZoneInfo:
        return ZoneInfo(self.hotel_timezone)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
