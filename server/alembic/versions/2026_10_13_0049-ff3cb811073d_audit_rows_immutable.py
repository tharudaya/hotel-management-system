"""audit rows immutable

audit_log and audit_events become append-only at the database level: a trigger
rejects every UPDATE and DELETE, whoever runs it (including FK actions such as
ON DELETE SET NULL, so a user with audit rows cannot be deleted either; users are
only ever deactivated). TRUNCATE is not affected (dev reset and tests use it).
Also: audit timestamps default to clock_timestamp() (the moment of the insert)
instead of now() (the transaction start), so entries written in one transaction keep
their real order; and audit_events.occurred_at is indexed for the newest-first viewer.

Revision ID: ff3cb811073d
Revises: 8b1b6189f0dc
Create Date: 2026-10-13 00:49:02.468297

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff3cb811073d"
down_revision: str | Sequence[str] | None = "8b1b6189f0dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_TABLES: tuple[str, ...] = ("audit_log", "audit_events")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION audit_rows_are_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Rows in % cannot be changed or deleted (% refused)',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for table in AUDIT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION audit_rows_are_immutable()"
        )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.execute("ALTER TABLE audit_log ALTER COLUMN created_at SET DEFAULT clock_timestamp()")
    op.execute("ALTER TABLE audit_events ALTER COLUMN occurred_at SET DEFAULT clock_timestamp()")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE audit_events ALTER COLUMN occurred_at SET DEFAULT now()")
    op.execute("ALTER TABLE audit_log ALTER COLUMN created_at SET DEFAULT now()")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    for table in AUDIT_TABLES:
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    op.execute("DROP FUNCTION audit_rows_are_immutable()")
