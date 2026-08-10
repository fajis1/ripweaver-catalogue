"""Add installation identity, metered lookups, credits, and support orders.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installations",
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("installation_id"),
        sa.UniqueConstraint("token_sha256"),
    )
    op.add_column(
        "submissions", sa.Column("installation_id", sa.String(length=36), nullable=True)
    )
    op.create_foreign_key(
        "fk_submissions_installation",
        "submissions",
        "installations",
        ["installation_id"],
        ["installation_id"],
    )
    op.create_index(
        op.f("ix_submissions_installation_id"),
        "submissions",
        ["installation_id"],
        unique=False,
    )
    op.create_table(
        "credit_ledger",
        sa.Column("entry_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("bucket", sa.String(length=16), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("reference_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.installation_id"]),
        sa.PrimaryKeyConstraint("entry_id"),
        sa.UniqueConstraint(
            "installation_id",
            "reason",
            "reference_id",
            "bucket",
            name="uq_credit_reference",
        ),
    )
    op.create_index(
        op.f("ix_credit_ledger_installation_id"),
        "credit_ledger",
        ["installation_id"],
        unique=False,
    )
    op.create_table(
        "lookup_events",
        sa.Column("lookup_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("credit_source", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("support_prompt_version", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.installation_id"]),
        sa.PrimaryKeyConstraint("lookup_id"),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key_sha256",
            name="uq_lookup_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_lookup_events_created_at"),
        "lookup_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_lookup_events_installation_id"),
        "lookup_events",
        ["installation_id"],
        unique=False,
    )
    op.create_table(
        "support_orders",
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("support_rate_cents", sa.Integer(), nullable=False),
        sa.Column("credit_count", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("terms_version", sa.String(length=32), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=255), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.installation_id"]),
        sa.PrimaryKeyConstraint("order_id"),
        sa.UniqueConstraint("provider_payment_id"),
        sa.UniqueConstraint("provider_session_id"),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key_sha256",
            name="uq_support_order_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_support_orders_installation_id"),
        "support_orders",
        ["installation_id"],
        unique=False,
    )
    op.create_table(
        "payment_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("provider_object_id", sa.String(length=255), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("payment_events")
    op.drop_index(
        op.f("ix_support_orders_installation_id"), table_name="support_orders"
    )
    op.drop_table("support_orders")
    op.drop_index(op.f("ix_lookup_events_installation_id"), table_name="lookup_events")
    op.drop_index(op.f("ix_lookup_events_created_at"), table_name="lookup_events")
    op.drop_table("lookup_events")
    op.drop_index(op.f("ix_credit_ledger_installation_id"), table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_index(op.f("ix_submissions_installation_id"), table_name="submissions")
    op.drop_constraint("fk_submissions_installation", "submissions", type_="foreignkey")
    op.drop_column("submissions", "installation_id")
    op.drop_table("installations")
