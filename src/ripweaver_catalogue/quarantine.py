"""Pending-only persistence for validated, untrusted catalogue submissions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ingest import VALIDATION_VERSION
from .models import QuarantinedSubmission
from .schemas import (
    DiscSubmissionInput,
    QuarantineReceipt,
    QuarantineSummary,
)


class QuarantineConflictError(RuntimeError):
    """Raised when an immutable quarantine operation conflicts."""


class QuarantineNotFoundError(RuntimeError):
    """Raised when a private quarantine record does not exist."""


def canonical_payload(payload: DiscSubmissionInput) -> tuple[str, str]:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, digest


def _receipt(row: QuarantinedSubmission) -> QuarantineReceipt:
    return QuarantineReceipt(
        submission_id=row.submission_id,
        content_hash=row.content_hash,
        payload_sha256=row.payload_sha256,
        status=row.status,
        validation_version=row.validation_version,
    )


def quarantine_submission(
    session: Session,
    payload: DiscSubmissionInput,
    *,
    installation_id: str,
    idempotency_key: str,
    client_version: str,
) -> QuarantineReceipt:
    """Store one validated claim without creating publication-linked rows."""

    encoded, payload_sha256 = canonical_payload(payload)
    idempotency_sha256 = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(QuarantinedSubmission).where(
            QuarantinedSubmission.installation_id == installation_id,
            QuarantinedSubmission.idempotency_key_sha256 == idempotency_sha256,
        )
    )
    if existing is not None:
        if existing.payload_sha256 != payload_sha256:
            raise QuarantineConflictError(
                "Idempotency key was already used for a different submission"
            )
        return _receipt(existing)

    row = QuarantinedSubmission(
        submission_id=str(uuid.uuid4()),
        installation_id=installation_id,
        content_hash=payload.content_hash,
        payload_sha256=payload_sha256,
        payload_json=encoded,
        idempotency_key_sha256=idempotency_sha256,
        client_version=client_version,
        validation_version=VALIDATION_VERSION,
        status="pending",
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise QuarantineConflictError(
            "Submission could not be quarantined idempotently"
        ) from exc
    return _receipt(row)


def list_quarantine(
    session: Session, *, status: str = "pending", limit: int = 100
) -> tuple[QuarantineSummary, ...]:
    rows = session.scalars(
        select(QuarantinedSubmission)
        .where(QuarantinedSubmission.status == status)
        .order_by(QuarantinedSubmission.created_at)
        .limit(limit)
    ).all()
    return tuple(
        QuarantineSummary(
            **_receipt(row).model_dump(),
            client_version=row.client_version,
            rejection_code=row.rejection_code,
            created_at=row.created_at.isoformat(),
            reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
        )
        for row in rows
    )


def reject_quarantined_submission(
    session: Session, submission_id: str, *, reason_code: str
) -> QuarantineReceipt:
    row = session.get(QuarantinedSubmission, submission_id)
    if row is None:
        raise QuarantineNotFoundError("Quarantined submission does not exist")
    if row.status == "rejected":
        if row.rejection_code != reason_code:
            raise QuarantineConflictError(
                "Submission was already rejected with a different reason"
            )
        return _receipt(row)
    if row.status != "pending":
        raise QuarantineConflictError("Only pending submissions can be rejected")
    row.status = "rejected"
    row.rejection_code = reason_code
    row.reviewed_at = datetime.now(UTC)
    session.commit()
    return _receipt(row)
