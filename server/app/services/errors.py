"""Domain errors raised by services. The API layer maps them to HTTP responses."""

from typing import ClassVar


class DomainError(Exception):
    """A business rule rejected the request.

    `code` is a stable machine-readable identifier; `message` is for people.
    """

    default_code: ClassVar[str] = "conflict"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code


class NotFoundError(DomainError):
    default_code = "not_found"


class InvalidTransitionError(DomainError):
    """The entity's current state does not allow this action."""

    default_code = "invalid_transition"


class VersionConflictError(DomainError):
    """Someone else changed the row since the client last read it."""

    default_code = "version_conflict"


class PermissionDeniedError(DomainError):
    """The user lacks a permission that only the service can check (depends on data)."""

    default_code = "forbidden"


class GoneError(DomainError):
    """The thing existed but can no longer be used (expired or used link): HTTP 410."""

    default_code = "gone"


class InvalidInputError(DomainError):
    """Well-formed input that breaks a business rule (e.g. password policy): HTTP 422."""

    default_code = "invalid_input"


class TooManyRequestsError(DomainError):
    """Throttled: HTTP 429 with Retry-After."""

    default_code = "too_many_requests"

    def __init__(self, message: str, *, retry_after_seconds: int, code: str | None = None) -> None:
        super().__init__(message, code=code)
        self.retry_after_seconds = retry_after_seconds


class ServiceUnavailableError(DomainError):
    """A dependency (e.g. the email service) failed: HTTP 503."""

    default_code = "service_unavailable"
