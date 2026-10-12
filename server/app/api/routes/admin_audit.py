"""Audit log endpoint (thin: the query lives in services/audit_log.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentPropertyId, require_permission
from app.core.permissions import P
from app.db.session import SessionDep
from app.schemas.audit import AuditPage, AuditQuery
from app.services.audit_log import list_audit_entries

router: APIRouter = APIRouter(prefix="/admin/audit", tags=["admin: audit"])


@router.get("", dependencies=[Depends(require_permission(P.AUDIT_VIEW))])
async def list_audit(
    db: SessionDep, property_id: CurrentPropertyId, query: Annotated[AuditQuery, Query()]
) -> AuditPage:
    """Data changes at this property, newest first; filter by target to see one record's history."""
    return await list_audit_entries(db, property_id, query)
