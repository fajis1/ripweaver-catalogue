"""Public support terms and deterministic credit calculations."""

from .config import Settings
from .schemas import SupportPolicy

SUPPORT_POLICY_VERSION = "2026-08-10"
SUPPORT_MESSAGE = (
    "Support RipWeaver and receive automatic catalogue lookup credits, "
    "or continue each lookup manually without paying."
)
AVAILABILITY_DISCLOSURE = (
    "RipWeaver is independently operated on a best-effort, as-is, and "
    "as-available basis. A support payment does not guarantee future "
    "maintenance, technical support, uptime, data preservation, or continued "
    "availability. Backups are maintained, but outages, data loss, or permanent "
    "discontinuation remain possible. Unused credits may become unusable if the "
    "service ends and have no cash value."
)
REFUND_DISCLOSURE = (
    "Payments are generally final once credits are issued, except where required "
    "by law or payment-network rules, or for duplicate, unauthorized, or "
    "incorrectly fulfilled payments."
)


def build_support_policy(settings: Settings) -> SupportPolicy:
    return SupportPolicy(
        policy_version=SUPPORT_POLICY_VERSION,
        terms_version=settings.support_terms_version,
        minimum_amount_cents=settings.support_minimum_cents,
        minimum_rate_cents=settings.support_rate_min_cents,
        maximum_rate_cents=settings.support_rate_max_cents,
        default_rate_cents=settings.support_default_rate_cents,
        monthly_automatic_lookups=settings.monthly_automatic_lookups,
        payments_enabled=settings.stripe_is_ready,
        support_message=SUPPORT_MESSAGE,
        availability_disclosure=AVAILABILITY_DISCLOSURE,
        refund_disclosure=REFUND_DISCLOSURE,
    )


def calculate_support_credits(
    *, amount_cents: int, support_rate_cents: int, settings: Settings
) -> int:
    if amount_cents < settings.support_minimum_cents:
        raise ValueError("Support amount is below the configured minimum")
    if (
        not settings.support_rate_min_cents
        <= support_rate_cents
        <= settings.support_rate_max_cents
    ):
        raise ValueError("Support rate is outside the configured range")
    return amount_cents // support_rate_cents
