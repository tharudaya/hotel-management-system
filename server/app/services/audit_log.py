"""Reading the audit log (writing is services/audit.record_change)."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User
from app.schemas.audit import AuditActor, AuditEntry, AuditPage, AuditQuery


async def list_audit_entries(db: AsyncSession, property_id: int, query: AuditQuery) -> AuditPage:
    """Changes at the property (plus property-less system changes), newest first."""
    base = (
        select(AuditLog, User)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(or_(AuditLog.property_id == property_id, AuditLog.property_id.is_(None)))
    )
    if query.target_type is not None:
        base = base.where(AuditLog.target_type == query.target_type)
    if query.target_id is not None:
        base = base.where(AuditLog.target_id == query.target_id)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.execute(
            base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    return AuditPage(
        items=[
            AuditEntry(
                id=entry.id,
                created_at=entry.created_at,
                action=entry.action,
                actor=(
                    AuditActor(id=actor.id, username=actor.username, full_name=actor.full_name)
                    if actor
                    else None
                ),
                target_type=entry.target_type,
                target_id=entry.target_id,
                before=entry.before,
                after=entry.after,
            )
            for entry, actor in rows
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )
