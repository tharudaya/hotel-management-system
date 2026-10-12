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
