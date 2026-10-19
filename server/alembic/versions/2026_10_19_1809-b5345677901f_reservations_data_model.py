"""reservations data model

Data model for staff-made reservations (see "Reservations" in CLAUDE.md):

properties: + currency (ISO 4217, default SEK), max_stay_nights (default 30),
  booking_horizon_days (default 730).
room_types: + base_rate_minor (nightly default price in minor units, default 0).
rooms: + unique (id, property_id), target of the reservations' room foreign key.
guests: + property_id (backfilled from the guest's reservations, else the first
  property), notes, created_at, updated_at; emails trimmed and lower-cased (checked);
  generated phone_digits and search_text (trigram index, needs pg_trgm) for search.
reservations: + property_id (from the room type), source (backfilled "other"),
  total_minor, currency, version, created_by, checked_in_at, checked_out_at,
  cancelled_at, cancelled_by, cancellation_reason. Room type, room and guest foreign
  keys become composite with property_id, so all four belong to one property.
  Confirmation codes are regenerated in the new format (8 characters from
  23456789ABCDEFGHJKMNPQRSTUVWXYZ, checked). Status timestamps must match the status
  (backfilled from the audit log, else from the stay dates). A GiST index serves
  availability counts. The room overlap exclusion constraint already exists.
reservation_nights: one row per night with the rate snapshot; backfilled from the
  room type base rate, and reservations.total_minor is their sum.
idempotency_keys: new table.
Permission reservation.override_rate, granted to the Admin, General manager and Front
  office manager system roles.

Downgrade drops everything added here. It is lossy: regenerated confirmation codes
and lower-cased guest emails are kept, nights, prices and status timestamps are lost.

Revision ID: b5345677901f
Revises: cc4383465ea3
Create Date: 2026-10-19 18:09:16.904820

"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b5345677901f"
down_revision: str | Sequence[str] | None = "cc4383465ea3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- frozen data (as of this revision) ---------------------------------------------

CODE_ALPHABET: str = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_PATTERN: str = "^[2-9A-HJKMNP-Z]{8}$"

# The development seed's room types get their seed rates, so an existing dev database
# matches a fresh seed. Any other room type starts at 0 until an admin sets its rate.
SEED_BASE_RATES: dict[str, int] = {"STD": 129_900, "DLX": 179_900, "STE": 249_900}

OVERRIDE_RATE: tuple[str, str, str] = (
    "reservation.override_rate",
    "Change the nightly price from the room type default",
    "reservations",
)
OVERRIDE_RATE_ROLES: tuple[str, ...] = ("Admin", "General manager", "Front office manager")

PHONE_DIGITS_SQL: str = "regexp_replace(phone, '[^0-9]', '', 'g')"
SEARCH_TEXT_SQL: str = (
    "lower(first_name || ' ' || last_name || ' ' || coalesce(email, '') || ' ' || "
    f"coalesce({PHONE_DIGITS_SQL}, ''))"
)
ACTIVE_STATUSES_SQL: str = "status IN ('confirmed', 'checked_in')"

RESERVATION_CHECKS: dict[str, str] = {
    "ck_reservations_total_non_negative": "total_minor >= 0",
    "ck_reservations_currency_iso_code": "currency ~ '^[A-Z]{3}$'",
    "ck_reservations_confirmation_code_format": f"confirmation_code ~ '{CODE_PATTERN}'",
    "ck_reservations_checked_in_at_matches_status": (
        "(checked_in_at IS NOT NULL) = (status IN ('checked_in', 'checked_out'))"
    ),
    "ck_reservations_checked_out_at_matches_status": (
        "(checked_out_at IS NOT NULL) = (status = 'checked_out')"
    ),
    "ck_reservations_cancelled_at_matches_status": (
        "(cancelled_at IS NOT NULL) = (status IN ('cancelled', 'no_show'))"
    ),
    "ck_reservations_cancellation_details_need_cancelled_at": (
        "cancelled_at IS NOT NULL OR (cancelled_by IS NULL AND cancellation_reason IS NULL)"
    ),
}

# (name, local columns, referred table) for the composite property-scoped keys.
COMPOSITE_FKS: list[tuple[str, str, str]] = [
    ("fk_reservations_room_type_id_property_id_room_types", "room_type_id", "room_types"),
    ("fk_reservations_room_id_property_id_rooms", "room_id", "rooms"),
    ("fk_reservations_guest_id_property_id_guests", "guest_id", "guests"),
]
# The single-column keys they replace (restored by the downgrade).
SIMPLE_FKS: list[tuple[str, str, str]] = [
    ("fk_reservations_room_type_id_room_types", "room_type_id", "room_types"),
    ("fk_reservations_room_id_rooms", "room_id", "rooms"),
    ("fk_reservations_guest_id_guests", "guest_id", "guests"),
]

RESERVATION_SOURCE = postgresql.ENUM(
    "phone", "email", "walk_in", "other", name="reservation_source", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    if context.is_offline_mode():
        raise RuntimeError("This migration moves data and needs a live database (no --sql).")
    conn = op.get_bind()
    has_guests = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM guests)")).scalar()
    has_property = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM properties)")).scalar()
    if has_guests and not has_property:
        raise RuntimeError("Guests exist but no property does; cannot assign them one.")

    # Trigram index for guest search; btree_gist (exclusion constraint, availability
    # index) exists since the first migration.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- properties -------------------------------------------------------------------
    op.add_column(
        "properties",
        sa.Column("currency", sa.String(length=3), server_default="SEK", nullable=False),
    )
    op.add_column(
        "properties",
        sa.Column("max_stay_nights", sa.SmallInteger(), server_default="30", nullable=False),
    )
    op.add_column(
        "properties",
        sa.Column(
            "booking_horizon_days", sa.SmallInteger(), server_default="730", nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_properties_currency_iso_code"), "properties", "currency ~ '^[A-Z]{3}$'"
    )
    op.create_check_constraint(
        op.f("ck_properties_max_stay_nights_range"),
        "properties",
        "max_stay_nights BETWEEN 1 AND 365",
    )
    op.create_check_constraint(
        op.f("ck_properties_booking_horizon_days_range"),
        "properties",
        "booking_horizon_days BETWEEN 1 AND 1825",
    )

    # --- room_types and rooms ---------------------------------------------------------
    op.add_column(
        "room_types",
        sa.Column("base_rate_minor", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_room_types_base_rate_non_negative"), "room_types", "base_rate_minor >= 0"
    )
    for code, rate in SEED_BASE_RATES.items():
        conn.execute(
            sa.text("UPDATE room_types SET base_rate_minor = :rate WHERE code = :code"),
            {"rate": rate, "code": code},
        )
    op.create_unique_constraint("uq_rooms_id_property_id", "rooms", ["id", "property_id"])

    # --- guests -----------------------------------------------------------------------
    op.add_column("guests", sa.Column("property_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE guests g SET property_id = coalesce(
            (SELECT min(rt.property_id)
               FROM reservations r JOIN room_types rt ON rt.id = r.room_type_id
              WHERE r.guest_id = g.id),
            (SELECT min(id) FROM properties))
        """
    )
    op.alter_column("guests", "property_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_guests_property_id_properties"),
        "guests",
        "properties",
        ["property_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_guests_id_property_id", "guests", ["id", "property_id"])
    op.add_column("guests", sa.Column("notes", sa.Text(), nullable=True))
    for column in ("created_at", "updated_at"):
        op.add_column(
            "guests",
            sa.Column(
                column, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
        )
    op.execute("UPDATE guests SET email = nullif(lower(btrim(email)), '') WHERE email IS NOT NULL")
    op.create_check_constraint(
        op.f("ck_guests_email_normalised"),
        "guests",
        "email IS NULL OR (email = lower(btrim(email)) AND email <> '')",
    )
    op.add_column(
        "guests",
        sa.Column(
            "phone_digits",
            sa.String(length=50),
            sa.Computed(PHONE_DIGITS_SQL, persisted=True),
            nullable=True,
        ),
    )
    op.add_column(
        "guests",
        sa.Column(
            "search_text",
            sa.Text(),
            sa.Computed(SEARCH_TEXT_SQL, persisted=True),
            nullable=False,
        ),
    )
    op.drop_index("ix_guests_last_name_first_name", table_name="guests")
    op.drop_index(op.f("ix_guests_email"), table_name="guests")
    op.create_index(
        "ix_guests_property_id_last_name_first_name",
        "guests",
        ["property_id", "last_name", "first_name"],
    )
    op.create_index("ix_guests_property_id_email", "guests", ["property_id", "email"])
    op.create_index(
        "ix_guests_property_id_phone_digits", "guests", ["property_id", "phone_digits"]
    )
    op.create_index(
        "ix_guests_search_text_trgm",
        "guests",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )

    # --- reservations: new columns ----------------------------------------------------
    RESERVATION_SOURCE.create(conn)
    op.add_column("reservations", sa.Column("property_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE reservations r SET property_id = rt.property_id "
        "FROM room_types rt WHERE rt.id = r.room_type_id"
    )
    op.alter_column("reservations", "property_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_reservations_property_id_properties"),
        "reservations",
        "properties",
        ["property_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Server defaults only to fill existing rows; dropped below (the app always sets them).
    op.add_column(
        "reservations",
        sa.Column("source", RESERVATION_SOURCE, server_default="other", nullable=False),
    )
    op.add_column(
        "reservations",
        sa.Column("total_minor", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("reservations", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column(
        "reservations",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    for column in ("created_by", "cancelled_by"):
        op.add_column("reservations", sa.Column(column, sa.Integer(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_reservations_{column}_users"),
            "reservations",
            "users",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
    for column in ("checked_in_at", "checked_out_at", "cancelled_at"):
        op.add_column(
            "reservations", sa.Column(column, sa.DateTime(timezone=True), nullable=True)
        )
    op.add_column("reservations", sa.Column("cancellation_reason", sa.Text(), nullable=True))

    # Status timestamps: the audit log knows real check-in/out times; otherwise assume
    # the usual times (15:00 / 11:00 hotel time) on the stay dates.
    op.execute(
        """
        UPDATE reservations r SET checked_in_at = coalesce(
            (SELECT min(a.created_at) FROM audit_log a
              WHERE a.target_type = 'reservation' AND a.target_id = r.id::text
                AND a.action = 'reservation.checked_in'),
            (r.check_in_date + time '15:00') AT TIME ZONE p.time_zone)
        FROM properties p
        WHERE p.id = r.property_id AND r.status IN ('checked_in', 'checked_out')
        """
    )
    op.execute(
        """
        UPDATE reservations r SET checked_out_at = coalesce(
            (SELECT max(a.created_at) FROM audit_log a
              WHERE a.target_type = 'reservation' AND a.target_id = r.id::text
                AND a.action = 'reservation.checked_out'),
            (r.check_out_date + time '11:00') AT TIME ZONE p.time_zone)
        FROM properties p
        WHERE p.id = r.property_id AND r.status = 'checked_out'
        """
    )
    op.execute(
        "UPDATE reservations SET cancelled_at = updated_at "
        "WHERE status IN ('cancelled', 'no_show')"
    )

    # Confirmation codes in the new format. Existing codes count as taken, so a new
    # code never collides with a row not yet updated.
    rows = conn.execute(sa.text("SELECT id, confirmation_code FROM reservations")).all()
    taken = {code for _, code in rows}
    for reservation_id, _ in rows:
        while (code := "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))) in taken:
            pass
        taken.add(code)
        conn.execute(
            sa.text("UPDATE reservations SET confirmation_code = :code WHERE id = :id"),
            {"code": code, "id": reservation_id},
        )
    op.alter_column(
        "reservations",
        "confirmation_code",
        type_=sa.String(length=8),
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )

    # --- reservation_nights -----------------------------------------------------------
    op.create_table(
        "reservation_nights",
        sa.Column("reservation_id", sa.Integer(), nullable=False),
        sa.Column("night_date", sa.Date(), nullable=False),
        sa.Column("rate_minor", sa.Integer(), nullable=False),
        sa.CheckConstraint("rate_minor >= 0", name=op.f("ck_reservation_nights_rate_non_negative")),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name=op.f("fk_reservation_nights_reservation_id_reservations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("reservation_id", "night_date", name=op.f("pk_reservation_nights")),
    )
    op.execute(
        """
        INSERT INTO reservation_nights (reservation_id, night_date, rate_minor)
        SELECT r.id, night::date, rt.base_rate_minor
          FROM reservations r
          JOIN room_types rt ON rt.id = r.room_type_id
         CROSS JOIN LATERAL generate_series(
               r.check_in_date::timestamp, (r.check_out_date - 1)::timestamp, interval '1 day'
           ) AS night
        """
    )
    op.execute(
        """
        UPDATE reservations r SET
            total_minor = coalesce(
                (SELECT sum(n.rate_minor) FROM reservation_nights n WHERE n.reservation_id = r.id),
                0),
            currency = p.currency
        FROM properties p WHERE p.id = r.property_id
        """
    )
    op.alter_column("reservations", "currency", nullable=False)
    op.alter_column("reservations", "source", server_default=None)
    op.alter_column("reservations", "total_minor", server_default=None)

    # --- reservations: keys, checks, indexes ------------------------------------------
    for name, _, _ in SIMPLE_FKS:
        op.drop_constraint(op.f(name), "reservations", type_="foreignkey")
    for name, column, table in COMPOSITE_FKS:
        op.create_foreign_key(
            name,
            "reservations",
            table,
            [column, "property_id"],
            ["id", "property_id"],
            ondelete="RESTRICT",
        )
    for name, condition in RESERVATION_CHECKS.items():
        op.create_check_constraint(op.f(name), "reservations", condition)
    op.create_index(
        "ix_reservations_availability",
        "reservations",
        ["property_id", "room_type_id", sa.text("daterange(check_in_date, check_out_date)")],
        postgresql_using="gist",
        postgresql_where=sa.text(ACTIVE_STATUSES_SQL),
    )

    # --- idempotency_keys -------------------------------------------------------------
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_reference", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_idempotency_keys_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("key", "user_id", name="uq_idempotency_keys_key_user_id"),
    )
    op.create_index(
        op.f("ix_idempotency_keys_created_at"), "idempotency_keys", ["created_at"], unique=False
    )

    # --- permission -------------------------------------------------------------------
    # The startup sync would insert the key and grant it to Admin; the other two roles
    # get it here, as the default role definitions now include it.
    key, description, group = OVERRIDE_RATE
    conn.execute(
        sa.text(
            'INSERT INTO permissions (key, description, "group") VALUES (:key, :description, '
            ":group) ON CONFLICT (key) DO NOTHING"
        ),
        {"key": key, "description": description, "group": group},
    )
    conn.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_key) "
            "SELECT id, :key FROM roles WHERE is_system AND name = ANY(:names) "
            "ON CONFLICT DO NOTHING"
        ),
        {"key": key, "names": list(OVERRIDE_RATE_ROLES)},
    )


def downgrade() -> None:
    """Downgrade schema."""
    if context.is_offline_mode():
        raise RuntimeError("This migration moves data and needs a live database (no --sql).")
    conn = op.get_bind()

    # --- permission -------------------------------------------------------------------
    # (If the app still lists the key, its startup sync puts it back for Admin.)
    conn.execute(
        sa.text("DELETE FROM permissions WHERE key = :key"), {"key": OVERRIDE_RATE[0]}
    )  # role_permissions rows go with it (ON DELETE CASCADE)

    # --- idempotency_keys -------------------------------------------------------------
    op.drop_index(op.f("ix_idempotency_keys_created_at"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")

    # --- reservations -----------------------------------------------------------------
    op.drop_index("ix_reservations_availability", table_name="reservations")
    for name in RESERVATION_CHECKS:
        op.drop_constraint(op.f(name), "reservations", type_="check")
    for name, _, _ in COMPOSITE_FKS:
        op.drop_constraint(name, "reservations", type_="foreignkey")
    for name, column, table in SIMPLE_FKS:
        op.create_foreign_key(
            op.f(name), "reservations", table, [column], ["id"], ondelete="RESTRICT"
        )
    op.drop_table("reservation_nights")
    op.alter_column(
        "reservations",
        "confirmation_code",
        type_=sa.String(length=20),
        existing_type=sa.String(length=8),
        existing_nullable=False,
    )
    for column in ("created_by", "cancelled_by"):
        op.drop_constraint(
            op.f(f"fk_reservations_{column}_users"), "reservations", type_="foreignkey"
        )
    for column in (
        "cancellation_reason",
        "cancelled_by",
        "cancelled_at",
        "checked_out_at",
        "checked_in_at",
        "created_by",
        "version",
        "currency",
        "total_minor",
        "source",
    ):
        op.drop_column("reservations", column)
    op.drop_constraint(
        op.f("fk_reservations_property_id_properties"), "reservations", type_="foreignkey"
    )
    op.drop_column("reservations", "property_id")
    RESERVATION_SOURCE.drop(conn)

    # --- guests -----------------------------------------------------------------------
    op.drop_index("ix_guests_search_text_trgm", table_name="guests")
    op.drop_index("ix_guests_property_id_phone_digits", table_name="guests")
    op.drop_index("ix_guests_property_id_email", table_name="guests")
    op.drop_index("ix_guests_property_id_last_name_first_name", table_name="guests")
    op.create_index(op.f("ix_guests_email"), "guests", ["email"], unique=False)
    op.create_index(
        "ix_guests_last_name_first_name", "guests", ["last_name", "first_name"], unique=False
    )
    op.drop_column("guests", "search_text")
    op.drop_column("guests", "phone_digits")
    op.drop_constraint(op.f("ck_guests_email_normalised"), "guests", type_="check")
    for column in ("updated_at", "created_at", "notes"):
        op.drop_column("guests", column)
    op.drop_constraint("uq_guests_id_property_id", "guests", type_="unique")
    op.drop_constraint(op.f("fk_guests_property_id_properties"), "guests", type_="foreignkey")
    op.drop_column("guests", "property_id")
    # pg_trgm is left installed: other objects may depend on it, and it is harmless.

    # --- room_types and rooms ---------------------------------------------------------
    op.drop_constraint("uq_rooms_id_property_id", "rooms", type_="unique")
    op.drop_constraint(op.f("ck_room_types_base_rate_non_negative"), "room_types", type_="check")
    op.drop_column("room_types", "base_rate_minor")

    # --- properties -------------------------------------------------------------------
    for name in (
        "ck_properties_booking_horizon_days_range",
        "ck_properties_max_stay_nights_range",
        "ck_properties_currency_iso_code",
    ):
        op.drop_constraint(op.f(name), "properties", type_="check")
    for column in ("booking_horizon_days", "max_stay_nights", "currency"):
        op.drop_column("properties", column)
