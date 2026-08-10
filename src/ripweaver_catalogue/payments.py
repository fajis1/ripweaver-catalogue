"""Stripe-hosted checkout boundary with verified webhook parsing."""

from dataclasses import dataclass
from typing import Protocol

import stripe


class PaymentProviderError(RuntimeError):
    """A redacted payment-provider failure safe for API handling."""


@dataclass(frozen=True)
class CheckoutSession:
    session_id: str
    checkout_url: str


@dataclass(frozen=True)
class VerifiedPaymentEvent:
    event_id: str
    event_type: str
    session_id: str
    payment_id: str | None
    amount_total: int | None
    currency: str | None
    paid: bool


class CheckoutProvider(Protocol):
    def create_checkout(
        self,
        *,
        order_id: str,
        installation_id: str,
        amount_cents: int,
        credit_count: int,
        support_rate_cents: int,
        terms_version: str,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession: ...

    def verify_webhook(
        self, payload: bytes, signature: str | None
    ) -> VerifiedPaymentEvent: ...


class StripeCheckoutProvider:
    def __init__(self, *, secret_key: str, webhook_secret: str) -> None:
        self._client = stripe.StripeClient(secret_key)
        self._webhook_secret = webhook_secret

    def create_checkout(
        self,
        *,
        order_id: str,
        installation_id: str,
        amount_cents: int,
        credit_count: int,
        support_rate_cents: int,
        terms_version: str,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        try:
            session = self._client.v1.checkout.sessions.create(
                {
                    "mode": "payment",
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "line_items": [
                        {
                            "quantity": 1,
                            "price_data": {
                                "currency": "usd",
                                "unit_amount": amount_cents,
                                "product_data": {
                                    "name": "Support RipWeaver",
                                    "description": (
                                        f"{credit_count} automatic catalogue lookup "
                                        "credits at "
                                        f"${support_rate_cents / 100:.2f} each"
                                    ),
                                },
                            },
                        }
                    ],
                    "metadata": {
                        "order_id": order_id,
                        "installation_id": installation_id,
                        "terms_version": terms_version,
                    },
                },
                options={"idempotency_key": idempotency_key},
            )
        except Exception as exc:
            raise PaymentProviderError(
                f"Payment checkout failed safely ({type(exc).__name__})"
            ) from exc
        if not session.id or not session.url:
            raise PaymentProviderError("Payment checkout returned no hosted URL")
        return CheckoutSession(session_id=session.id, checkout_url=session.url)

    def verify_webhook(
        self, payload: bytes, signature: str | None
    ) -> VerifiedPaymentEvent:
        if not signature:
            raise PaymentProviderError("Payment webhook signature is missing")
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
            data = event["data"]["object"]
            event_type = str(event["type"])
            session_id = str(data["id"])
        except Exception as exc:
            raise PaymentProviderError(
                f"Payment webhook verification failed ({type(exc).__name__})"
            ) from exc
        payment_status = str(data.get("payment_status") or "")
        return VerifiedPaymentEvent(
            event_id=str(event["id"]),
            event_type=event_type,
            session_id=session_id,
            payment_id=(
                str(data.get("payment_intent"))
                if data.get("payment_intent") is not None
                else None
            ),
            amount_total=(
                int(data.get("amount_total"))
                if data.get("amount_total") is not None
                else None
            ),
            currency=(
                str(data.get("currency")).lower() if data.get("currency") else None
            ),
            paid=payment_status == "paid",
        )
