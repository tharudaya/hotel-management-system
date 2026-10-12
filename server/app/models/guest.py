from sqlalchemy import Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Guest(Base):
    __tablename__ = "guests"
    __table_args__ = (
        # Front desk looks guests up by name.
        Index("ix_guests_last_name_first_name", "last_name", "first_name"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))

    def __repr__(self) -> str:
        return f"<Guest {self.first_name} {self.last_name}>"
