"""Transactional catalogue, consensus, and legacy moderation operations."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .consensus import (
    add_assertions,
    consensus_summary,
    get_consensus_disc,
    lock_consensus_scope,
    recompute_consensus,
)
from .models import CatalogueDisc, DiscRevision, DiscTitle, Submission
from .schemas import (
    DiscRecord,
    DiscSubmissionInput,
    SubmissionReceipt,
    SubmissionSummary,
)


class CatalogueConflictError(RuntimeError):
    """Raised when an immutable moderation operation conflicts."""


class CatalogueNotFoundError(RuntimeError):
    """Raised when a requested private record does not exist."""


def canonical_payload(payload: DiscSubmissionInput) -> tuple[str, str]:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _receipt(session: Session, row: Submission) -> SubmissionReceipt:
    return SubmissionReceipt(
        submission_id=row.submission_id,
        content_hash=row.content_hash,
        payload_sha256=row.payload_sha256,
        status=row.status,
        consensus=(
            consensus_summary(session, row.content_hash)
            if row.status == "accepted"
            else None
        ),
    )


def record_trusted_legacy_proposal(
    session: Session,
    payload: DiscSubmissionInput,
    *,
    trusted_internal: bool,
    installation_id: str | None,
    idempotency_key: str,
    client_version: str,
    consensus_credit_threshold: float = 0.90,
) -> SubmissionReceipt:
    """Seed historical reviewed/consensus data outside the public ingest path.

    Public requests must use ``quarantine_submission`` instead. This explicit
    guard prevents an accidental route binding from silently restoring the old
    untrusted-input-to-consensus path.
    """

    if trusted_internal is not True:
        raise CatalogueConflictError(
            "Legacy catalogue writes require an explicit trusted internal caller"
        )
    encoded, payload_sha256 = canonical_payload(payload)
    idempotency_sha256 = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(Submission).where(
            Submission.idempotency_key_sha256 == idempotency_sha256
        )
    )
    if existing is not None:
        if existing.payload_sha256 != payload_sha256:
            raise CatalogueConflictError(
                "Idempotency key was already used for a different proposal"
            )
        return _receipt(session, existing)

    row = Submission(
        submission_id=str(uuid.uuid4()),
        installation_id=installation_id,
        content_hash=payload.content_hash,
        payload_sha256=payload_sha256,
        payload_json=encoded,
        idempotency_key_sha256=idempotency_sha256,
        client_version=client_version,
        status="accepted" if payload.schema_version == 2 else "pending",
    )
    try:
        if payload.schema_version == 2:
            if installation_id is None:
                raise CatalogueConflictError(
                    "Consensus contributions require an installation identity"
                )
            lock_consensus_scope(session, payload.content_hash)
        session.add(row)
        if payload.schema_version == 2:
            session.flush()
            add_assertions(session, row, payload)
            session.flush()
            recompute_consensus(
                session,
                payload.content_hash,
                credit_threshold=consensus_credit_threshold,
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CatalogueConflictError(
            "Proposal could not be stored idempotently"
        ) from exc
    return _receipt(session, row)


def get_disc(session: Session, content_hash: str) -> DiscRecord | None:
    normalized = content_hash.upper()
    consensus = get_consensus_disc(session, normalized)
    if consensus is not None:
        return consensus
    disc = session.get(CatalogueDisc, normalized)
    if disc is None or disc.latest_revision < 1:
        return None
    revision = session.scalar(
        select(DiscRevision).where(
            DiscRevision.content_hash == normalized,
            DiscRevision.revision == disc.latest_revision,
        )
    )
    if revision is None:
        return None
    payload = DiscSubmissionInput.model_validate_json(revision.payload_json)
    return DiscRecord(
        **payload.model_dump(),
        revision=revision.revision,
        payload_sha256=revision.payload_sha256,
    )


def list_submissions(
    session: Session, *, status: str = "pending", limit: int = 100
) -> tuple[SubmissionSummary, ...]:
    rows = session.scalars(
        select(Submission)
        .where(Submission.status == status)
        .order_by(Submission.created_at)
        .limit(limit)
    ).all()
    return tuple(
        SubmissionSummary(
            **_receipt(session, row).model_dump(),
            client_version=row.client_version,
            rejection_code=row.rejection_code,
            created_at=row.created_at.isoformat(),
            reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
        )
        for row in rows
    )


def approve_submission(session: Session, submission_id: str) -> DiscRecord:
    from .accounts import grant_contribution_credit

    submission = session.get(Submission, submission_id)
    if submission is None:
        raise CatalogueNotFoundError("Submission does not exist")
    if submission.status == "approved":
        existing = get_disc(session, submission.content_hash)
        if existing is None or existing.payload_sha256 != submission.payload_sha256:
            raise CatalogueConflictError("Approved submission has no active revision")
        return existing
    if submission.status != "pending":
        raise CatalogueConflictError("Only pending submissions can be approved")

    payload = DiscSubmissionInput.model_validate_json(submission.payload_json)
    disc = session.get(CatalogueDisc, payload.content_hash)
    if disc is None:
        disc = CatalogueDisc(
            content_hash=payload.content_hash,
            media_type=payload.media_type.value,
            latest_revision=0,
        )
        session.add(disc)
        session.flush()
    elif disc.media_type != payload.media_type.value:
        raise CatalogueConflictError("Disc media type conflicts with reviewed history")

    duplicate = session.scalar(
        select(DiscRevision).where(
            DiscRevision.content_hash == payload.content_hash,
            DiscRevision.payload_sha256 == submission.payload_sha256,
        )
    )
    now = datetime.now(UTC)
    if duplicate is not None:
        disc.latest_revision = duplicate.revision
        submission.status = "approved"
        submission.reviewed_at = now
        grant_contribution_credit(
            session,
            installation_id=submission.installation_id,
            submission_id=submission.submission_id,
        )
        session.commit()
        result = get_disc(session, payload.content_hash)
        if result is None:
            raise CatalogueConflictError("Approved revision is unavailable")
        return result

    next_revision = disc.latest_revision + 1
    revision_id = str(uuid.uuid4())
    revision = DiscRevision(
        revision_id=revision_id,
        content_hash=payload.content_hash,
        revision=next_revision,
        payload_sha256=submission.payload_sha256,
        payload_json=submission.payload_json,
        approved_at=now,
    )
    session.add(revision)
    session.flush()
    session.add_all(
        DiscTitle(
            revision_id=revision_id,
            title_index=title.title_index,
            source_file=title.source_file,
            segment_map=",".join(title.segment_map) if title.segment_map else None,
            duration_seconds=title.duration_seconds,
            size_bytes=title.size_bytes,
            classification=title.classification.value,
            series_name=title.series_name,
            season_number=title.season_number,
            episode_number=title.episode_number,
            episode_title=title.episode_title,
            movie_title=title.movie_title,
            movie_year=title.movie_year,
        )
        for title in payload.titles
    )
    disc.latest_revision = next_revision
    submission.status = "approved"
    submission.reviewed_at = now
    grant_contribution_credit(
        session,
        installation_id=submission.installation_id,
        submission_id=submission.submission_id,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CatalogueConflictError("Concurrent approval created a conflict") from exc
    result = get_disc(session, payload.content_hash)
    if result is None:
        raise CatalogueConflictError("Approved revision is unavailable")
    return result


def reject_submission(
    session: Session, submission_id: str, *, reason_code: str
) -> SubmissionReceipt:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise CatalogueNotFoundError("Submission does not exist")
    if submission.status == "rejected":
        if submission.rejection_code != reason_code:
            raise CatalogueConflictError(
                "Submission was already rejected with a different reason"
            )
        return _receipt(session, submission)
    if submission.status != "pending":
        raise CatalogueConflictError("Only pending submissions can be rejected")
    submission.status = "rejected"
    submission.rejection_code = reason_code
    submission.reviewed_at = datetime.now(UTC)
    session.commit()
    return _receipt(session, submission)
