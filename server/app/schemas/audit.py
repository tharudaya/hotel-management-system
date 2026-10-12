"""Read models for /api/admin/audit (the audit_log of data changes)."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Annotated[str | None, Field(max_length=50)] = Field(
        default=None, description='What changed, e.g. "user" or "role"'
    )
    target_id: Annotated[str | None, Field(max_length=64)] = None
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 25


class AuditActor(BaseModel):
    id: int
    username: str
    full_name: str


class AuditEntry(BaseModel):
    id: int
    created_at: datetime
    action: str
    # None for changes made by the system (migrations, seed).
    actor: AuditActor | None
    target_type: str
    target_id: str
    # Only the fields that changed; never secrets.
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class AuditPage(BaseModel):
    """Newest first."""

    items: list[AuditEntry]
    total: int
    page: int
    page_size: int
