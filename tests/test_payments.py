from types import SimpleNamespace

import pytest

from ripweaver_catalogue import payments
from ripweaver_catalogue.payments import PaymentProviderError, StripeCheckoutProvider


def test_stripe_checkout_uses_hosted_payment_and_provider_idempotency(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class _Sessions:
        def create(self, params, options=None):
            observed["params"] = params
            observed["options"] = options
            return SimpleNamespace(
                id="cs_test_synthetic",
                url="https://checkout.stripe.test/synthetic",
            )

    fake_client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=_Sessions()))
    )

    def _client(secret_key: str):
        assert secret_key == "sk_test_synthetic"
        return fake_client

    monkeypatch.setattr(payments.stripe, "StripeClient", _client)
    provider = StripeCheckoutProvider(
        secret_key="sk_test_synthetic",
        webhook_secret="whsec_synthetic",
    )

    receipt = provider.create_checkout(
        order_id="order-synthetic",
        installation_id="installation-synthetic",
        amount_cents=1_000,
        credit_count=1_000,
        support_rate_cents=1,
        terms_version="2026-08-10",
        success_url="https://ripweaver.com/support/success",
        cancel_url="https://ripweaver.com/support/cancelled",
        idempotency_key="support-order-synthetic",
    )

    assert receipt.session_id == "cs_test_synthetic"
    assert receipt.checkout_url.startswith("https://")
    assert observed["options"] == {"idempotency_key": "support-order-synthetic"}
    params = observed["params"]
    assert params["mode"] == "payment"
    assert params["line_items"][0]["price_data"]["unit_amount"] == 1_000
    assert params["metadata"] == {
        "order_id": "order-synthetic",
        "installation_id": "installation-synthetic",
        "terms_version": "2026-08-10",
    }


def test_stripe_webhook_is_verified_before_payment_fields_are_accepted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        payments.stripe,
        "StripeClient",
        lambda _key: SimpleNamespace(),
    )

    def _construct(payload, signature, secret):
        assert payload == b"synthetic-event"
        assert signature == "synthetic-signature"
        assert secret == "whsec_synthetic"
        return {
            "id": "evt_synthetic",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_synthetic",
                    "payment_intent": "pi_test_synthetic",
                    "payment_status": "paid",
                    "amount_total": 1_000,
                    "currency": "USD",
                }
            },
        }

    monkeypatch.setattr(payments.stripe.Webhook, "construct_event", _construct)
    provider = StripeCheckoutProvider(
        secret_key="sk_test_synthetic",
        webhook_secret="whsec_synthetic",
    )

    event = provider.verify_webhook(b"synthetic-event", "synthetic-signature")

    assert event.event_id == "evt_synthetic"
    assert event.session_id == "cs_test_synthetic"
    assert event.amount_total == 1_000
    assert event.currency == "usd"
    assert event.paid is True

    with pytest.raises(PaymentProviderError, match="signature is missing"):
        provider.verify_webhook(b"synthetic-event", None)
