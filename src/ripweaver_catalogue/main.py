"""RipWeaver Catalogue FastAPI composition."""

import re
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import __version__
from .auth import bearer_dependency
from .config import Settings, get_settings
from .database import build_engine, build_session_factory, session_dependency
from .repository import (
    CatalogueConflictError,
    CatalogueNotFoundError,
    approve_submission,
    get_disc,
    list_submissions,
    reject_submission,
    submit_proposal,
)
from .schemas import (
    DiscRecord,
    DiscSubmissionInput,
    RejectionRequest,
    SubmissionReceipt,
    SubmissionSummary,
)


class SchemaCapabilities(BaseModel):
    schema_version: Literal[1] = 1
    service_version: str
    public_lookup: bool = True
    authenticated_submissions: bool = True
    attachments_accepted: bool = False
    media_accepted: bool = False


def create_app(
    *, settings: Settings | None = None, engine: Engine | None = None
) -> FastAPI:
    selected_settings = settings or get_settings()
    selected_engine = engine or build_engine(selected_settings)
    session_factory = build_session_factory(selected_engine)
    get_session = session_dependency(session_factory)
    SessionDependency = Annotated[Session, Depends(get_session)]
    require_submission = bearer_dependency(
        selected_settings.submission_token, role="Submission"
    )
    require_admin = bearer_dependency(selected_settings.admin_token, role="Admin")

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
        return SchemaCapabilities(service_version=__version__)

    @application.get("/v1/discs/{content_hash}", response_model=DiscRecord)
    def lookup_disc(
        content_hash: str,
        response: Response,
        session: SessionDependency,
    ) -> DiscRecord:
        if re.fullmatch(r"[0-9A-Fa-f]{32}", content_hash) is None:
            raise HTTPException(status_code=422, detail="Content hash is invalid")
        record = get_disc(session, content_hash)
        if record is None:
            raise HTTPException(status_code=404, detail="Disc is not catalogued")
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["ETag"] = f'"{record.payload_sha256}"'
        return record

    @application.post(
        "/v1/submissions",
        response_model=SubmissionReceipt,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_submission)],
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
    ) -> SubmissionReceipt:
        try:
            return submit_proposal(
                session,
                payload,
                idempotency_key=idempotency_key,
                client_version=client_version,
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
            Literal["pending", "approved", "rejected"], Query(alias="status")
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
