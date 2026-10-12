from sqlalchemy import CheckConstraint, Identity, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, pg_enum
from app.models.enums import UserRole


class User(CreatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Usernames are stored lowercased so uniqueness is case-insensitive;
        # the service layer normalizes input before lookups and inserts.
        CheckConstraint("username = lower(username)", name="username_lowercase"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    def __repr__(self) -> str:
        return f"<User {self.username!r} role={self.role}>"
