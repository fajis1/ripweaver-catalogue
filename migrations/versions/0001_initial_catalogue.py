"""Create private catalogue, revisions, titles, and moderation queue.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalogue_discs",
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("latest_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("content_hash"),
    )
    op.create_table(
        "submissions",
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rejection_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("submission_id"),
        sa.UniqueConstraint("idempotency_key_sha256", name="uq_submission_idempotency"),
    )
    op.create_index(
        op.f("ix_submissions_content_hash"),
        "submissions",
        ["content_hash"],
        unique=False,
    )
    op.create_table(
        "disc_revisions",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_hash"], ["catalogue_discs.content_hash"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint("content_hash", "payload_sha256", name="uq_disc_payload"),
        sa.UniqueConstraint("content_hash", "revision", name="uq_disc_revision"),
    )
    op.create_index(
        op.f("ix_disc_revisions_content_hash"),
        "disc_revisions",
        ["content_hash"],
        unique=False,
    )
    op.create_table(
        "disc_titles",
        sa.Column("title_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("title_index", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.String(length=96), nullable=False),
        sa.Column("segment_map", sa.String(length=2000), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("series_name", sa.String(length=200), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("episode_title", sa.String(length=300), nullable=True),
        sa.Column("movie_title", sa.String(length=300), nullable=True),
        sa.Column("movie_year", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["disc_revisions.revision_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("title_id"),
        sa.UniqueConstraint("revision_id", "title_index", name="uq_revision_title"),
    )


def downgrade() -> None:
    op.drop_table("disc_titles")
    op.drop_index(op.f("ix_disc_revisions_content_hash"), table_name="disc_revisions")
    op.drop_table("disc_revisions")
    op.drop_index(op.f("ix_submissions_content_hash"), table_name="submissions")
    op.drop_table("submissions")
    op.drop_table("catalogue_discs")
