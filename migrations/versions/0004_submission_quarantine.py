"""Add an isolated pending-only submission quarantine.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_quarantine",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("validation_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rejection_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'rejected')",
            name="ck_submission_quarantine_status",
        ),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.installation_id"]),
        sa.PrimaryKeyConstraint("submission_id"),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key_sha256",
            name="uq_quarantine_installation_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_submission_quarantine_content_hash"),
        "submission_quarantine",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_submission_quarantine_installation_id"),
        "submission_quarantine",
        ["installation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_submission_quarantine_installation_id"),
        table_name="submission_quarantine",
    )
    op.drop_index(
        op.f("ix_submission_quarantine_content_hash"),
        table_name="submission_quarantine",
    )
    op.drop_table("submission_quarantine")
