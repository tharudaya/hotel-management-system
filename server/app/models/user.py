from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class User(CreatedAtMixin, Base):
    """A staff account.

    Users are never deleted, only deactivated (deactivated_at set). A user can sign in
    only while not deactivated and not past expires_at. What they may do comes from
    their role at a property (user_property_roles), not from this table.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Usernames and emails are stored lowercased so uniqueness is case-insensitive;
        # the service layer normalizes input before lookups and inserts.
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    # Null for invited users who have not set a password yet (they cannot log in).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    # Optional: not every staff member has an email address. Unique when present.
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    job_title: Mapped[str | None] = mapped_column(String(100))
    employee_id: Mapped[str | None] = mapped_column(String(50))
    # Temporary staff: no access after this moment.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    def __repr__(self) -> str:
        return f"<User {self.username!r}>"
