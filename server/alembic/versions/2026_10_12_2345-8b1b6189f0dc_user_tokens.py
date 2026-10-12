"""user tokens

Single-use invite and password reset tokens (only their keyed hash is stored).

Revision ID: 8b1b6189f0dc
Revises: f3955642c7f5
Create Date: 2026-10-12 23:45:05.755111

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b1b6189f0dc"
down_revision: str | Sequence[str] | None = "f3955642c7f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('invite', 'password_reset')", name=op.f("ck_user_tokens_purpose_known")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_user_tokens_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_tokens_token_hash")),
    )
    op.create_index(op.f("ix_user_tokens_user_id"), "user_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_user_tokens_user_id"), table_name="user_tokens")
    op.drop_table("user_tokens")
