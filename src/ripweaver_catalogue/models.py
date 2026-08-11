"""Private PostgreSQL persistence models."""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class Installation(Base):
    __tablename__ = "installations"

    installation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("installations.installation_id"), nullable=True, index=True
    )
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


class ConsensusAssertion(Base):
    __tablename__ = "consensus_assertions"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "title_index", name="uq_assertion_submission_title"
        ),
    )

    assertion_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("installations.installation_id"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title_index: Mapped[int] = mapped_column(Integer, nullable=False)
    structural_key: Mapped[str] = mapped_column(String(64), nullable=False)
    assignment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title_json: Mapped[str] = mapped_column(Text, nullable=False)
    match_source: Mapped[str] = mapped_column(String(32), nullable=False)
    independent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConsensusDisc(Base):
    __tablename__ = "consensus_discs"

    content_hash: Mapped[str] = mapped_column(String(32), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    release_name: Mapped[str | None] = mapped_column(String(300))
    edition: Mapped[str | None] = mapped_column(String(200))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unresolved_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    whole_disc_consistent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ConsensusItem(Base):
    __tablename__ = "consensus_items"

    content_hash: Mapped[str] = mapped_column(
        ForeignKey("consensus_discs.content_hash", ondelete="CASCADE"),
        primary_key=True,
    )
    title_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    winning_structural_key: Mapped[str | None] = mapped_column(String(64))
    winning_assignment_key: Mapped[str | None] = mapped_column(String(64))
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    runner_up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winning_title_json: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class CreditLedgerEntry(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "reason",
            "reference_id",
            "bucket",
            name="uq_credit_reference",
        ),
    )

    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("installations.installation_id"), nullable=False, index=True
    )
    bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LookupEvent(Base):
    __tablename__ = "lookup_events"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "idempotency_key_sha256",
            name="uq_lookup_idempotency",
        ),
    )

    lookup_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("installations.installation_id"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    credit_source: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    support_prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class SupportOrder(Base):
    __tablename__ = "support_orders"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "idempotency_key_sha256",
            name="uq_support_order_idempotency",
        ),
    )

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("installations.installation_id"), nullable=False, index=True
    )
    idempotency_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    support_rate_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False)
    terms_accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
