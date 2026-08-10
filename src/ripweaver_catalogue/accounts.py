"""Private installation identity, quota, credit-ledger, and support operations."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    CreditLedgerEntry,
    Installation,
    LookupEvent,
    PaymentEvent,
    SupportOrder,
)
from .payments import CheckoutSession, VerifiedPaymentEvent
from .repository import CatalogueConflictError, get_disc
from .schemas import (
    InstallationReceipt,
    LookupRequest,
    LookupResponse,
    SupportCheckoutInput,
    SupportCheckoutReceipt,
    UsageSummary,
)
from .support import calculate_support_credits


class InstallationAuthenticationError(RuntimeError):
    """Raised when an installation bearer token is absent or invalid."""


class SupportRequiredError(RuntimeError):
    def __init__(self, usage: UsageSummary) -> None:
        self.usage = usage
        super().__init__("Automatic catalogue lookup requires support confirmation")


class SupportCheckoutError(RuntimeError):
    """Raised when a checkout or fulfillment cannot be accepted safely."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def register_installation(session: Session) -> InstallationReceipt:
    token = f"rwc_{secrets.token_urlsafe(32)}"
    row = Installation(
        installation_id=str(uuid.uuid4()),
        token_sha256=_sha256(token),
    )
    session.add(row)
    session.commit()
    return InstallationReceipt(
        installation_id=row.installation_id,
        access_token=token,
    )


def authenticate_installation(session: Session, token: str) -> Installation:
    if not token.startswith("rwc_") or len(token) < 36:
        raise InstallationAuthenticationError("Installation token is invalid")
    row = session.scalar(
        select(Installation).where(Installation.token_sha256 == _sha256(token))
    )
    if row is None or row.disabled_at is not None:
        raise InstallationAuthenticationError("Installation token is invalid")
    row.last_seen_at = datetime.now(UTC)
    return row


def _month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _credit_balance(session: Session, installation_id: str, bucket: str) -> int:
    value = session.scalar(
        select(func.coalesce(func.sum(CreditLedgerEntry.delta), 0)).where(
            CreditLedgerEntry.installation_id == installation_id,
            CreditLedgerEntry.bucket == bucket,
        )
    )
    return max(0, int(value or 0))


def usage_summary(
    session: Session,
    installation_id: str,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> UsageSummary:
    current = now or datetime.now(UTC)
    monthly_used = int(
        session.scalar(
            select(func.count(LookupEvent.lookup_id)).where(
                LookupEvent.installation_id == installation_id,
                LookupEvent.credit_source == "monthly",
                LookupEvent.created_at >= _month_start(current),
            )
        )
        or 0
    )
    monthly_remaining = max(0, settings.monthly_automatic_lookups - monthly_used)
    contribution = _credit_balance(session, installation_id, "contribution")
    purchased = _credit_balance(session, installation_id, "purchased")
    return UsageSummary(
        monthly_limit=settings.monthly_automatic_lookups,
        monthly_used=monthly_used,
        monthly_remaining=monthly_remaining,
        contribution_credits=contribution,
        purchased_credits=purchased,
        total_automatic_remaining=monthly_remaining + contribution + purchased,
    )


def serve_lookup(
    session: Session,
    installation: Installation,
    content_hash: str,
    request: LookupRequest,
    *,
    idempotency_key: str,
    settings: Settings,
) -> LookupResponse | None:
    record = get_disc(session, content_hash)
    if record is None:
        return None

    idempotency_sha256 = _sha256(idempotency_key)
    existing = session.scalar(
        select(LookupEvent).where(
            LookupEvent.installation_id == installation.installation_id,
            LookupEvent.idempotency_key_sha256 == idempotency_sha256,
        )
    )
    if existing is not None:
        if (
            existing.content_hash != content_hash.upper()
            or existing.mode != request.mode
        ):
            raise CatalogueConflictError(
                "Idempotency key was already used for a different lookup"
            )
        return LookupResponse(
            disc=record,
            usage=usage_summary(
                session, installation.installation_id, settings=settings
            ),
            credit_source=existing.credit_source,
        )

    previously_served = session.scalar(
        select(LookupEvent.lookup_id).where(
            LookupEvent.installation_id == installation.installation_id,
            LookupEvent.content_hash == content_hash.upper(),
        )
    )
    if previously_served is not None:
        return LookupResponse(
            disc=record,
            usage=usage_summary(
                session, installation.installation_id, settings=settings
            ),
            credit_source="cached",
        )

    locked = session.scalar(
        select(Installation)
        .where(Installation.installation_id == installation.installation_id)
        .with_for_update()
    )
    if locked is None or locked.disabled_at is not None:
        raise InstallationAuthenticationError("Installation token is invalid")

    before = usage_summary(session, installation.installation_id, settings=settings)
    if request.mode == "manual":
        if request.support_prompt_version != settings.support_terms_version:
            raise CatalogueConflictError("Displayed support prompt is out of date")
        credit_source = "manual"
    elif before.monthly_remaining > 0:
        credit_source = "monthly"
    elif before.contribution_credits > 0:
        credit_source = "contribution"
    elif before.purchased_credits > 0:
        credit_source = "purchased"
    else:
        raise SupportRequiredError(before)

    lookup_id = str(uuid.uuid4())
    session.add(
        LookupEvent(
            lookup_id=lookup_id,
            installation_id=installation.installation_id,
            content_hash=content_hash.upper(),
            mode=request.mode,
            credit_source=credit_source,
            idempotency_key_sha256=idempotency_sha256,
            support_prompt_version=request.support_prompt_version,
        )
    )
    if credit_source in {"contribution", "purchased"}:
        session.add(
            CreditLedgerEntry(
                entry_id=str(uuid.uuid4()),
                installation_id=installation.installation_id,
                bucket=credit_source,
                delta=-1,
                reason="automatic_lookup",
                reference_id=lookup_id,
            )
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CatalogueConflictError("Lookup could not be stored idempotently") from exc
    return LookupResponse(
        disc=record,
        usage=usage_summary(session, installation.installation_id, settings=settings),
        credit_source=credit_source,
    )


def grant_contribution_credit(
    session: Session, *, installation_id: str | None, submission_id: str
) -> None:
    if installation_id is None:
        return
    existing = session.scalar(
        select(CreditLedgerEntry).where(
            CreditLedgerEntry.installation_id == installation_id,
            CreditLedgerEntry.reason == "approved_submission",
            CreditLedgerEntry.reference_id == submission_id,
            CreditLedgerEntry.bucket == "contribution",
        )
    )
    if existing is None:
        session.add(
            CreditLedgerEntry(
                entry_id=str(uuid.uuid4()),
                installation_id=installation_id,
                bucket="contribution",
                delta=1,
                reason="approved_submission",
                reference_id=submission_id,
            )
        )


def prepare_support_order(
    session: Session,
    installation: Installation,
    request: SupportCheckoutInput,
    *,
    idempotency_key: str,
    settings: Settings,
) -> SupportOrder:
    if request.terms_version != settings.support_terms_version:
        raise SupportCheckoutError("Support terms have changed; review them again")
    try:
        credits = calculate_support_credits(
            amount_cents=request.amount_cents,
            support_rate_cents=request.support_rate_cents,
            settings=settings,
        )
    except ValueError as exc:
        raise SupportCheckoutError(str(exc)) from exc
    digest = _sha256(idempotency_key)
    existing = session.scalar(
        select(SupportOrder).where(
            SupportOrder.installation_id == installation.installation_id,
            SupportOrder.idempotency_key_sha256 == digest,
        )
    )
    if existing is not None:
        if (
            existing.amount_cents != request.amount_cents
            or existing.support_rate_cents != request.support_rate_cents
            or existing.terms_version != request.terms_version
        ):
            raise CatalogueConflictError(
                "Idempotency key was already used for a different support order"
            )
        return existing
    order = SupportOrder(
        order_id=str(uuid.uuid4()),
        installation_id=installation.installation_id,
        idempotency_key_sha256=digest,
        amount_cents=request.amount_cents,
        support_rate_cents=request.support_rate_cents,
        credit_count=credits,
        currency="usd",
        terms_version=request.terms_version,
        terms_accepted_at=datetime.now(UTC),
        status="pending",
    )
    session.add(order)
    session.commit()
    return order


def attach_checkout(
    session: Session, order: SupportOrder, checkout: CheckoutSession
) -> SupportOrder:
    if order.provider_session_id is not None:
        if (
            order.provider_session_id != checkout.session_id
            or order.checkout_url != checkout.checkout_url
        ):
            raise SupportCheckoutError("Support order already has another checkout")
        return order
    order.provider_session_id = checkout.session_id
    order.checkout_url = checkout.checkout_url
    order.status = "checkout_created"
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise SupportCheckoutError("Payment checkout session conflicts") from exc
    return order


def checkout_receipt(order: SupportOrder) -> SupportCheckoutReceipt:
    if order.checkout_url is None or order.status not in {"checkout_created", "paid"}:
        raise SupportCheckoutError("Payment checkout is not ready")
    return SupportCheckoutReceipt(
        order_id=order.order_id,
        amount_cents=order.amount_cents,
        support_rate_cents=order.support_rate_cents,
        automatic_lookup_credits=order.credit_count,
        checkout_url=order.checkout_url,
        status=order.status,
    )


def fulfill_support_order(session: Session, event: VerifiedPaymentEvent) -> None:
    if event.event_type not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        return
    if session.get(PaymentEvent, event.event_id) is not None:
        return
    order = session.scalar(
        select(SupportOrder)
        .where(SupportOrder.provider_session_id == event.session_id)
        .with_for_update()
    )
    if order is None:
        raise SupportCheckoutError("Payment session does not match a support order")
    if not event.paid:
        raise SupportCheckoutError("Payment session is not paid")
    if event.amount_total != order.amount_cents or event.currency != order.currency:
        raise SupportCheckoutError("Payment amount does not match the support order")

    session.add(
        PaymentEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            provider_object_id=event.session_id,
        )
    )
    if order.status != "paid":
        order.status = "paid"
        order.provider_payment_id = event.payment_id
        order.fulfilled_at = datetime.now(UTC)
        session.add(
            CreditLedgerEntry(
                entry_id=str(uuid.uuid4()),
                installation_id=order.installation_id,
                bucket="purchased",
                delta=order.credit_count,
                reason="support_payment",
                reference_id=order.order_id,
            )
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise SupportCheckoutError("Payment fulfillment conflicts") from exc
