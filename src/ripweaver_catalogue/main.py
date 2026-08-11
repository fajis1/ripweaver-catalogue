"""RipWeaver Catalogue FastAPI composition."""

import re
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import __version__
from .accounts import (
    InstallationAuthenticationError,
    SupportCheckoutError,
    SupportRequiredError,
    attach_checkout,
    authenticate_installation,
    checkout_receipt,
    fulfill_support_order,
    prepare_support_order,
    register_installation,
    serve_lookup,
    usage_summary,
)
from .auth import bearer_dependency
from .config import Settings, get_settings
from .consensus import get_consensus_help
from .database import build_engine, build_session_factory, session_dependency
from .models import Installation
from .payments import CheckoutProvider, PaymentProviderError, StripeCheckoutProvider
from .repository import (
    CatalogueConflictError,
    CatalogueNotFoundError,
    approve_submission,
    list_submissions,
    reject_submission,
    submit_proposal,
)
from .schemas import (
    DiscHelpRecord,
    DiscRecord,
    DiscSubmissionInput,
    InstallationReceipt,
    LookupRequest,
    LookupResponse,
    PaymentWebhookReceipt,
    RejectionRequest,
    SubmissionReceipt,
    SubmissionSummary,
    SupportCheckoutInput,
    SupportCheckoutReceipt,
    SupportPolicy,
    SupportRequired,
    UsageSummary,
)
from .support import build_support_policy


class SchemaCapabilities(BaseModel):
    schema_version: Literal[3] = 3
    service_version: str
    public_lookup: bool = False
    installation_registration: bool = True
    metered_lookup: bool = True
    manual_lookup_after_prompt: bool = True
    contribution_credits: bool = True
    support_checkout: bool
    authenticated_submissions: bool = True
    automatic_piecewise_consensus: bool = True
    provisional_help: bool = True
    independent_quorum: Literal[2] = 2
    human_moderation_required: bool = False
    attachments_accepted: bool = False
    media_accepted: bool = False


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    checkout_provider: CheckoutProvider | None = None,
) -> FastAPI:
    selected_settings = settings or get_settings()
    selected_engine = engine or build_engine(selected_settings)
    session_factory = build_session_factory(selected_engine)
    get_session = session_dependency(session_factory)
    SessionDependency = Annotated[Session, Depends(get_session)]
    require_admin = bearer_dependency(selected_settings.admin_token, role="Admin")
    selected_checkout_provider = checkout_provider
    if selected_checkout_provider is None and selected_settings.stripe_is_ready:
        selected_checkout_provider = StripeCheckoutProvider(
            secret_key=selected_settings.stripe_secret_key.get_secret_value(),
            webhook_secret=(selected_settings.stripe_webhook_secret.get_secret_value()),
        )

    def require_installation(
        session: SessionDependency,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Installation:
        scheme, separator, token = (authorization or "").partition(" ")
        if separator != " " or scheme.casefold() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Installation authentication is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            return authenticate_installation(session, token)
        except InstallationAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    application = FastAPI(
        title="RipWeaver Catalogue",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=selected_settings.allowed_hosts
    )

    @application.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "live"}

    @application.get("/health/ready")
    def health_ready(session: SessionDependency) -> dict[str, str]:
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database is unavailable",
            ) from exc
        return {"status": "ready"}

    @application.get("/v1/schema", response_model=SchemaCapabilities)
    def schema_capabilities() -> SchemaCapabilities:
        return SchemaCapabilities(
            service_version=__version__,
            support_checkout=selected_settings.stripe_is_ready,
        )

    @application.post(
        "/v1/installations/register",
        response_model=InstallationReceipt,
        status_code=status.HTTP_201_CREATED,
    )
    def create_installation(session: SessionDependency) -> InstallationReceipt:
        return register_installation(session)

    @application.get("/v1/support/policy", response_model=SupportPolicy)
    def support_policy() -> SupportPolicy:
        return build_support_policy(selected_settings)

    @application.get("/v1/account/usage", response_model=UsageSummary)
    def account_usage(
        session: SessionDependency,
        installation: Annotated[Installation, Depends(require_installation)],
    ) -> UsageSummary:
        return usage_summary(
            session, installation.installation_id, settings=selected_settings
        )

    @application.post("/v1/lookups/discs/{content_hash}", response_model=LookupResponse)
    def lookup_disc(
        content_hash: str,
        lookup_request: LookupRequest,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=200)
        ],
        session: SessionDependency,
        installation: Annotated[Installation, Depends(require_installation)],
    ) -> LookupResponse:
        if re.fullmatch(r"[0-9A-Fa-f]{32}", content_hash) is None:
            raise HTTPException(status_code=422, detail="Content hash is invalid")
        try:
            result = serve_lookup(
                session,
                installation,
                content_hash,
                lookup_request,
                idempotency_key=idempotency_key,
                settings=selected_settings,
            )
        except SupportRequiredError as exc:
            prompt = SupportRequired(
                message=(
                    "Automatic lookup credits are exhausted. Support RipWeaver, "
                    "contribute a reviewed disc, or continue this lookup manually."
                ),
                usage=exc.usage,
                policy=build_support_policy(selected_settings),
            )
            raise HTTPException(status_code=402, detail=prompt.model_dump()) from exc
        except CatalogueConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Disc is not catalogued")
        return result

    @application.get("/v1/help/discs/{content_hash}", response_model=DiscHelpRecord)
    def help_with_disc(
        content_hash: str,
        session: SessionDependency,
        _installation: Annotated[Installation, Depends(require_installation)],
    ) -> DiscHelpRecord:
        if re.fullmatch(r"[0-9A-Fa-f]{32}", content_hash) is None:
            raise HTTPException(status_code=422, detail="Content hash is invalid")
        result = get_consensus_help(session, content_hash)
        if result is None:
            raise HTTPException(status_code=404, detail="No candidate evidence exists")
        return result

    @application.post("/v1/support/checkout", response_model=SupportCheckoutReceipt)
    def create_support_checkout(
        payload: SupportCheckoutInput,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=200)
        ],
        session: SessionDependency,
        installation: Annotated[Installation, Depends(require_installation)],
    ) -> SupportCheckoutReceipt:
        if selected_checkout_provider is None or not selected_settings.stripe_is_ready:
            raise HTTPException(
                status_code=503,
                detail="Support payments are not configured yet",
            )
        try:
            order = prepare_support_order(
                session,
                installation,
                payload,
                idempotency_key=idempotency_key,
                settings=selected_settings,
            )
            if order.checkout_url is None:
                checkout = selected_checkout_provider.create_checkout(
                    order_id=order.order_id,
                    installation_id=installation.installation_id,
                    amount_cents=order.amount_cents,
                    credit_count=order.credit_count,
                    support_rate_cents=order.support_rate_cents,
                    terms_version=order.terms_version,
                    success_url=selected_settings.stripe_success_url,
                    cancel_url=selected_settings.stripe_cancel_url,
                    idempotency_key=f"support-order-{order.order_id}",
                )
                order = attach_checkout(session, order, checkout)
            return checkout_receipt(order)
        except CatalogueConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SupportCheckoutError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PaymentProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.post(
        "/v1/payments/stripe/webhook", response_model=PaymentWebhookReceipt
    )
    async def stripe_webhook(
        request: Request,
        session: SessionDependency,
        stripe_signature: Annotated[
            str | None, Header(alias="Stripe-Signature")
        ] = None,
    ) -> PaymentWebhookReceipt:
        if selected_checkout_provider is None or not selected_settings.stripe_is_ready:
            raise HTTPException(status_code=503, detail="Payments are not configured")
        try:
            event = selected_checkout_provider.verify_webhook(
                await request.body(), stripe_signature
            )
            fulfill_support_order(session, event)
        except PaymentProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SupportCheckoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return PaymentWebhookReceipt()

    @application.post(
        "/v1/submissions",
        response_model=SubmissionReceipt,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_submission(
        payload: DiscSubmissionInput,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=200)
        ],
        client_version: Annotated[
            str, Header(alias="X-RipWeaver-Version", min_length=1, max_length=64)
        ],
        session: SessionDependency,
        installation: Annotated[Installation, Depends(require_installation)],
    ) -> SubmissionReceipt:
        try:
            return submit_proposal(
                session,
                payload,
                installation_id=installation.installation_id,
                idempotency_key=idempotency_key,
                client_version=client_version,
                consensus_credit_threshold=(
                    selected_settings.consensus_credit_threshold
                ),
            )
        except CatalogueConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get(
        "/v1/admin/submissions",
        response_model=tuple[SubmissionSummary, ...],
        dependencies=[Depends(require_admin)],
    )
    def pending_submissions(
        session: SessionDependency,
        submission_status: Annotated[
            Literal["pending", "accepted", "approved", "rejected"],
            Query(alias="status"),
        ] = "pending",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> tuple[SubmissionSummary, ...]:
        return list_submissions(session, status=submission_status, limit=limit)

    @application.post(
        "/v1/admin/submissions/{submission_id}/approve",
        response_model=DiscRecord,
        dependencies=[Depends(require_admin)],
    )
    def approve(submission_id: str, session: SessionDependency) -> DiscRecord:
        try:
            return approve_submission(session, submission_id)
        except CatalogueNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CatalogueConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post(
        "/v1/admin/submissions/{submission_id}/reject",
        response_model=SubmissionReceipt,
        dependencies=[Depends(require_admin)],
    )
    def reject(
        submission_id: str,
        request: RejectionRequest,
        session: SessionDependency,
    ) -> SubmissionReceipt:
        try:
            return reject_submission(
                session, submission_id, reason_code=request.reason_code
            )
        except CatalogueNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CatalogueConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return application


app = create_app()
