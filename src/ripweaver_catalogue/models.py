"""Private PostgreSQL persistence models."""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class CatalogueDisc(Base):
    __tablename__ = "catalogue_discs"

    content_hash: Mapped[str] = mapped_column(String(32), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    latest_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    revisions: Mapped[list["DiscRevision"]] = relationship(
        back_populates="disc", cascade="all, delete-orphan"
    )


class DiscRevision(Base):
    __tablename__ = "disc_revisions"
    __table_args__ = (
        UniqueConstraint("content_hash", "revision", name="uq_disc_revision"),
        UniqueConstraint("content_hash", "payload_sha256", name="uq_disc_payload"),
    )

    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_hash: Mapped[str] = mapped_column(
        ForeignKey("catalogue_discs.content_hash", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    disc: Mapped[CatalogueDisc] = relationship(back_populates="revisions")
    titles: Mapped[list["DiscTitle"]] = relationship(
        back_populates="revision_record", cascade="all, delete-orphan"
    )


class DiscTitle(Base):
    __tablename__ = "disc_titles"
    __table_args__ = (
        UniqueConstraint("revision_id", "title_index", name="uq_revision_title"),
    )

    title_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("disc_revisions.revision_id", ondelete="CASCADE"), nullable=False
    )
    title_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file: Mapped[str] = mapped_column(String(96), nullable=False)
    segment_map: Mapped[str | None] = mapped_column(String(2000))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    series_name: Mapped[str | None] = mapped_column(String(200))
    season_number: Mapped[int | None] = mapped_column(Integer)
    episode_number: Mapped[int | None] = mapped_column(Integer)
    episode_title: Mapped[str | None] = mapped_column(String(300))
    movie_title: Mapped[str | None] = mapped_column(String(300))
    movie_year: Mapped[int | None] = mapped_column(Integer)

    revision_record: Mapped[DiscRevision] = relationship(back_populates="titles")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("idempotency_key_sha256", name="uq_submission_idempotency"),
    )

    submission_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    client_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
