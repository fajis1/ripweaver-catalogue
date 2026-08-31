from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ripweaver_catalogue.config import Settings
from ripweaver_catalogue.main import create_app
from ripweaver_catalogue.models import Base
from ripweaver_catalogue.payments import CheckoutSession, VerifiedPaymentEvent
from ripweaver_catalogue.repository import (
    approve_submission,
    record_trusted_legacy_proposal,
)
from ripweaver_catalogue.schemas import DiscSubmissionInput

ADMIN_TOKEN = "admin-token-that-is-distinct-and-at-least-32-characters"
CONTENT_HASH = "0123456789ABCDEF0123456789ABCDEF"
SECOND_CONTENT_HASH = "FEDCBA9876543210FEDCBA9876543210"


class FakeCheckoutProvider:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.event = VerifiedPaymentEvent(
            event_id="evt_paid_001",
            event_type="checkout.session.completed",
            session_id="cs_test_support_001",
            payment_id="pi_test_support_001",
            amount_total=1_000,
            currency="usd",
            paid=True,
        )

    def create_checkout(self, **kwargs) -> CheckoutSession:
        self.created.append(kwargs)
        return CheckoutSession(
            session_id="cs_test_support_001",
            checkout_url="https://checkout.stripe.test/session-001",
        )

    def verify_webhook(self, payload: bytes, signature: str | None):
        assert payload == b"synthetic-signed-event"
        assert signature == "synthetic-signature"
        return self.event


@pytest.fixture
def client_and_provider() -> Iterator[tuple[TestClient, FakeCheckoutProvider, Engine]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    provider = FakeCheckoutProvider()
    settings = Settings(
        database_url=SecretStr("sqlite+pysqlite://"),
        admin_token=SecretStr(ADMIN_TOKEN),
        monthly_automatic_lookups=1,
        support_payments_enabled=True,
        stripe_secret_key=SecretStr("sk_test_synthetic"),
        stripe_webhook_secret=SecretStr("whsec_synthetic"),
        allowed_hosts=["testserver"],
    )
    with TestClient(
        create_app(settings=settings, engine=engine, checkout_provider=provider)
    ) as client:
        yield client, provider, engine
    engine.dispose()


def register(client: TestClient) -> dict[str, str]:
    receipt = client.post("/v1/installations/register")
    assert receipt.status_code == 201
    return {"Authorization": f"Bearer {receipt.json()['access_token']}"}


def proposal(content_hash: str = CONTENT_HASH) -> dict[str, object]:
    return {
        "schema_version": 1,
        "content_hash": content_hash,
        "media_type": "bluray",
        "release_name": "Synthetic Series Disc",
        "edition": None,
        "titles": [
            {
                "title_index": 1,
                "source_file": "00001.mpls",
                "segment_map": ["46", "47"],
                "duration_seconds": 1440,
                "size_bytes": 2_000_000_000,
                "classification": "episode",
                "series_name": "Synthetic Series",
                "season_number": 1,
                "episode_number": 1,
                "episode_title": "Synthetic Pilot",
                "movie_title": None,
                "movie_year": None,
            }
        ],
    }


def seed_reviewed_discs(engine: Engine, *content_hashes: str) -> None:
    for index, content_hash in enumerate(content_hashes, start=1):
        with Session(engine) as session:
            created = record_trusted_legacy_proposal(
                session,
                DiscSubmissionInput.model_validate(proposal(content_hash)),
                trusted_internal=True,
                installation_id=None,
                idempotency_key=f"trusted-test-seed-{index:04d}",
                client_version="trusted-test-seed",
            )
            approved = approve_submission(session, created.submission_id)
            assert approved.status == "reviewed"


def lookup(
    client: TestClient,
    authentication: dict[str, str],
    *,
    key: str,
    mode: str = "automatic",
    content_hash: str = CONTENT_HASH,
) -> object:
    body: dict[str, object] = {"mode": mode}
    if mode == "manual":
        body["support_prompt_version"] = "2026-08-10"
    return client.post(
        f"/v1/lookups/discs/{content_hash}",
        json=body,
        headers={**authentication, "Idempotency-Key": key},
    )


def test_quota_requires_visible_prompt_but_manual_lookup_remains_available(
    client_and_provider,
) -> None:
    client, _provider, engine = client_and_provider
    seed_reviewed_discs(engine, CONTENT_HASH, SECOND_CONTENT_HASH)
    user = register(client)

    first = lookup(client, user, key="lookup-monthly-key-0001")
    assert first.status_code == 200
    assert first.json()["credit_source"] == "monthly"
    replay = lookup(client, user, key="lookup-monthly-key-0001")
    assert replay.status_code == 200
    assert replay.json()["usage"]["monthly_used"] == 1
    cached = lookup(client, user, key="lookup-cached-key-0002")
    assert cached.status_code == 200
    assert cached.json()["credit_source"] == "cached"

    blocked = lookup(
        client,
        user,
        key="lookup-support-key-0003",
        content_hash=SECOND_CONTENT_HASH,
    )
    assert blocked.status_code == 402
    prompt = blocked.json()["detail"]
    assert prompt["code"] == "support_confirmation_required"
    assert prompt["usage"]["total_automatic_remaining"] == 0
    assert "permanent discontinuation" in prompt["policy"]["availability_disclosure"]

    manual = lookup(
        client,
        user,
        key="lookup-manual-key-0004",
        mode="manual",
        content_hash=SECOND_CONTENT_HASH,
    )
    assert manual.status_code == 200
    assert manual.json()["credit_source"] == "manual"


def test_support_checkout_uses_selected_rate_and_fulfills_once(
    client_and_provider,
) -> None:
    client, provider, _engine = client_and_provider
    user = register(client)
    checkout = client.post(
        "/v1/support/checkout",
        json={
            "amount_cents": 1_000,
            "support_rate_cents": 1,
            "terms_version": "2026-08-10",
            "accept_best_effort_terms": True,
        },
        headers={**user, "Idempotency-Key": "support-checkout-key-0001"},
    )
    assert checkout.status_code == 200
    assert checkout.json()["automatic_lookup_credits"] == 1_000
    assert checkout.json()["checkout_url"].startswith("https://")
    assert provider.created[0]["amount_cents"] == 1_000
    assert provider.created[0]["credit_count"] == 1_000

    for _attempt in range(2):
        webhook = client.post(
            "/v1/payments/stripe/webhook",
            content=b"synthetic-signed-event",
            headers={"Stripe-Signature": "synthetic-signature"},
        )
        assert webhook.status_code == 200
    usage = client.get("/v1/account/usage", headers=user).json()
    assert usage["purchased_credits"] == 1_000


def test_checkout_rejects_old_terms_and_below_minimum(client_and_provider) -> None:
    client, _provider, _engine = client_and_provider
    user = register(client)
    common_headers = {**user, "Idempotency-Key": "support-invalid-key-0001"}
    old_terms = client.post(
        "/v1/support/checkout",
        json={
            "amount_cents": 1_000,
            "support_rate_cents": 10,
            "terms_version": "2025-01-01",
            "accept_best_effort_terms": True,
        },
        headers=common_headers,
    )
    assert old_terms.status_code == 422
    below_minimum = client.post(
        "/v1/support/checkout",
        json={
            "amount_cents": 999,
            "support_rate_cents": 10,
            "terms_version": "2026-08-10",
            "accept_best_effort_terms": True,
        },
        headers={**user, "Idempotency-Key": "support-invalid-key-0002"},
    )
    assert below_minimum.status_code == 422
