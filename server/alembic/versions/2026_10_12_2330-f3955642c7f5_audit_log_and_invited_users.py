"""audit log and invited users

- audit_log: one row per data change (who, what, before, after). Existing room and
  reservation change rows move here from audit_events, which keeps only
  authentication events from now on.
- users.password_hash becomes nullable: invited users have no password yet.

Downgrade copies room/reservation rows back to audit_events; user-administration rows
have no place in the old schema and are dropped. Invited users get a hash of a random
secret (nobody knows it, so they still cannot log in).

Revision ID: f3955642c7f5
Revises: b2491dacdce9
Create Date: 2026-10-12 23:30:41.902116

"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3955642c7f5"
down_revision: str | Sequence[str] | None = "b2491dacdce9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_log_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_audit_log_property_id_properties"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_actor_user_id"), "audit_log", ["actor_user_id"], unique=False
    )
    op.create_index(op.f("ix_audit_log_created_at"), "audit_log", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_log_property_id_created_at",
        "audit_log",
        ["property_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_log_target", "audit_log", ["target_type", "target_id", "created_at"], unique=False
    )
    op.alter_column("users", "password_hash", existing_type=sa.VARCHAR(length=255), nullable=True)

    # Move room and reservation change rows from audit_events to audit_log.
    op.execute(
        """
        INSERT INTO audit_log
            (property_id, actor_user_id, action, target_type, target_id,
             before, after, ip, user_agent, created_at)
        SELECT
            (SELECT min(id) FROM properties),
            user_id,
            event_type,
            CASE WHEN event_type LIKE 'reservation.%' THEN 'reservation' ELSE 'room' END,
            CASE WHEN event_type LIKE 'reservation.%'
                 THEN details->>'reservation_id' ELSE details->>'room_id' END,
            details->'before',
            COALESCE(details->'after', '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
                'room_id', details->'room_id',
                'room_number', details->'room_number',
                'confirmation_code', details->'confirmation_code'
            )),
            ip_address,
            user_agent,
            occurred_at
        FROM audit_events
        WHERE event_type LIKE 'reservation.%' OR event_type LIKE 'room.%'
        ORDER BY id
        """
    )
    op.execute(
        "DELETE FROM audit_events WHERE event_type LIKE 'reservation.%' OR event_type LIKE 'room.%'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Invited users had no password; the old schema needs one. Use a hash of a random
    # secret so they still cannot log in (and old code verifies it without errors).
    from pwdlib import PasswordHash

    unusable = PasswordHash.recommended().hash(secrets.token_urlsafe(32))
    op.get_bind().execute(
        sa.text("UPDATE users SET password_hash = :hash WHERE password_hash IS NULL"),
        {"hash": unusable},
    )
    op.alter_column("users", "password_hash", existing_type=sa.VARCHAR(length=255), nullable=False)

    # Room and reservation changes go back to audit_events; user admin rows are dropped.
    op.execute(
        """
        INSERT INTO audit_events
            (occurred_at, event_type, user_id, username, ip_address, user_agent, details)
        SELECT
            al.created_at,
            al.action,
            al.actor_user_id,
            u.username,
            al.ip,
            al.user_agent,
            jsonb_build_object('before', al.before, 'after', al.after)
              || CASE al.target_type
                   WHEN 'reservation' THEN jsonb_build_object('reservation_id', al.target_id::int)
                   ELSE jsonb_build_object('room_id', al.target_id::int)
                 END
        FROM audit_log al
        LEFT JOIN users u ON u.id = al.actor_user_id
        WHERE al.target_type IN ('reservation', 'room')
        ORDER BY al.id
        """
    )
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index("ix_audit_log_property_id_created_at", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_created_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_user_id"), table_name="audit_log")
    op.drop_table("audit_log")
