"""Add automatic piecewise contribution consensus.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consensus_assertions",
        sa.Column("assertion_id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("title_index", sa.Integer(), nullable=False),
        sa.Column("structural_key", sa.String(length=64), nullable=False),
        sa.Column("assignment_key", sa.String(length=64), nullable=False),
        sa.Column("title_json", sa.Text(), nullable=False),
        sa.Column("match_source", sa.String(length=32), nullable=False),
        sa.Column("independent", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.installation_id"]),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.submission_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("assertion_id"),
        sa.UniqueConstraint(
            "submission_id", "title_index", name="uq_assertion_submission_title"
        ),
    )
    op.create_index(
        op.f("ix_consensus_assertions_content_hash"),
        "consensus_assertions",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_consensus_assertions_installation_id"),
        "consensus_assertions",
        ["installation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_consensus_assertions_submission_id"),
        "consensus_assertions",
        ["submission_id"],
        unique=False,
    )
    op.create_table(
        "consensus_discs",
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("release_name", sa.String(length=300), nullable=True),
        sa.Column("edition", sa.String(length=200), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("confirmed_items", sa.Integer(), nullable=False),
        sa.Column("unresolved_items", sa.Integer(), nullable=False),
        sa.Column("whole_disc_consistent", sa.Boolean(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("content_hash"),
    )
    op.create_table(
        "consensus_items",
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("title_index", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("winning_structural_key", sa.String(length=64), nullable=True),
        sa.Column("winning_assignment_key", sa.String(length=64), nullable=True),
        sa.Column("support_count", sa.Integer(), nullable=False),
        sa.Column("runner_up_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("winning_title_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_hash"], ["consensus_discs.content_hash"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("content_hash", "title_index"),
    )


def downgrade() -> None:
    op.drop_table("consensus_items")
    op.drop_table("consensus_discs")
    op.drop_index(
        op.f("ix_consensus_assertions_submission_id"),
        table_name="consensus_assertions",
    )
    op.drop_index(
        op.f("ix_consensus_assertions_installation_id"),
        table_name="consensus_assertions",
    )
    op.drop_index(
        op.f("ix_consensus_assertions_content_hash"),
        table_name="consensus_assertions",
    )
    op.drop_table("consensus_assertions")
