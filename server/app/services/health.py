import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger: logging.Logger = logging.getLogger(__name__)


async def is_database_available(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError, OSError:
        # Log the detail server-side; callers only learn up/down.
        logger.exception("Database health check failed")
        return False
    return True
